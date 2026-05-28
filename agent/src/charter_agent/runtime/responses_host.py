"""Responses-protocol boot path.

Wraps the set of warm per-skill Agents in a `ResponsesHostServer` that
dispatches each request to the correct Agent based on the active project's
persisted skill identity.

Routing logic (per-request, in `_handle_inner_agent`):
  1. Pre-route: eagerly call `context.get_input_items()`, run
     `project_router.route_input_items()` so `state.active_project_id()` is
     set before we read the project log.  Cache the result so the wrapped
     getter returns it unchanged when the Agent calls it later.
  2. Resolve skill, in priority order:
        a. Preamble `skill=` field (client hint, always wins).
        b. Persisted `project_log["skill"]` (set on previous turn).
        c. **First-turn LLM classifier** — if neither (a) nor (b) is set,
           pick the best skill for the user's first message from the
           registered skills' `description` fields. If the classifier
           picks a non-default skill, persist it so future turns skip
           this step entirely (one LLM call per project lifetime).
        d. Default skill (`general`).
  3. Swap `self._agent` to the warm Agent for the resolved skill before
     calling `super()._handle_inner_agent()`, which uses `self._agent`
     internally.

Skill-to-skill transitions *within* a workflow (e.g. rfp_found →
charter-draft) are handled by the active orchestrator skill via
`invoke_skill` tool calls. The classifier above only picks the *top-level*
workflow skill once, on the first turn of a new project.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import state
from . import project_router, skill_loader

_DEFAULT_SKILL = "general"
_CLASSIFIER_INPUT_CAP = 2000  # chars of user text to send to classifier
_log = logging.getLogger(__name__)


def _resolve_project_skill() -> str | None:
    """Read the persisted skill name from the active project's log, or `None`
    if the project has no skill set yet (typically a brand-new project).
    """
    try:
        log_path = state.project_path("project_log.json")
        if state.exists(log_path):
            log = state.read_json(log_path)
            skill = log.get("skill")
            if skill and isinstance(skill, str):
                return skill
    except Exception as exc:  # noqa: BLE001
        _log.debug("_resolve_project_skill: could not read project log (%s)", exc)
    return None


def _first_user_text(items: Any) -> str:
    """Extract the user's first text-bearing input as a plain string.

    Mirrors the shape-tolerance of `project_router.route_input_items`.
    Returns "" if no text content is found.
    """
    if isinstance(items, str):
        return items
    if not items:
        return ""
    for it in items:
        content = getattr(it, "content", None)
        if content is None and isinstance(it, dict):
            content = it.get("content")
        if not content:
            continue
        blocks = content if isinstance(content, list) else [content]
        for blk in blocks:
            text = getattr(blk, "text", None)
            if text is None and isinstance(blk, dict):
                text = blk.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return ""


async def _classify_first_turn_skill(
    user_text: str,
    candidates: list[tuple[str, str]],
    default: str,
) -> str:
    """Pick the best skill for a first-turn message via a one-shot LLM call.

    Returns `default` (the general/fallback skill) on any error, empty input,
    or unrecognised model output. Cost: one short non-tool chat completion
    against the shared warm `FoundryChatClient`, only on the first turn of a
    new project — subsequent turns read the persisted skill from
    `project_log.json` and skip this entirely.
    """
    if not user_text.strip() or not candidates:
        return default

    from agent_framework import Message  # type: ignore[import-not-found]

    from . import foundry_host

    options_block = "\n".join(f"- {name}: {desc}" for name, desc in candidates)
    sys_prompt = (
        "You are a routing classifier. Given a user's first message in a new "
        "project and a list of available workflow skills, return ONLY the "
        "single skill name that best matches the user's intent — no quotes, "
        "no punctuation, no explanation. If no workflow skill clearly matches "
        "(e.g. the message is a greeting, off-topic chat, or ambiguous), "
        f"return: {default}\n\n"
        f"Available skills:\n{options_block}\n"
        f"- {default}: fallback for general chat, greetings, or unclear intent."
    )
    valid_names = {name for name, _ in candidates} | {default}
    raw = ""
    try:
        client = foundry_host.get_chat_client()
        resp = await client.get_response(
            messages=[
                Message("system", contents=[sys_prompt]),
                Message("user", contents=[user_text[:_CLASSIFIER_INPUT_CAP]]),
            ],
        )
        raw = (getattr(resp, "text", None) or "").strip()
        token = raw.split()[0] if raw else ""
        picked = "".join(c for c in token if c.isalnum() or c == "-").lower()
    except Exception as exc:  # noqa: BLE001
        _log.warning("classifier failed (%s); falling back to %r.", exc, default)
        return default

    if picked in valid_names:
        return picked
    _log.info(
        "classifier returned %r (raw=%r), not in known skills %s; using %r.",
        picked, raw, sorted(valid_names), default,
    )
    return default


def _persist_skill_choice(skill_name: str) -> None:
    """Write a minimal `project_log.json` stub so subsequent turns dispatch
    directly to `skill_name` without re-running the classifier.

    Idempotent — if the file already exists with a matching skill, no-op.
    """
    try:
        log_path = state.project_path("project_log.json")
        if state.exists(log_path):
            existing = state.read_json(log_path)
            if existing.get("skill") == skill_name:
                return
        pid = state.active_project_id()
        stub: dict[str, Any] = {
            "project_id": pid,
            "skill": skill_name,
            "status": "initializing",
            "tasks": [],
            "log_entries": [],
        }
        state.write_json(log_path, stub)
    except Exception as exc:  # noqa: BLE001
        _log.warning("could not persist skill choice %r (%s)", skill_name, exc)


def _build_resilient_host(all_agents: dict[str, Any], default_skill: str) -> Any:
    """Wrap `ResponsesHostServer` with per-request skill dispatch and history
    resilience.

    On every request:
    - Pre-routes the input to set the active project sandbox.
    - Resolves which warm Agent to use (preamble > project_log > classify > default).
    - Swaps `self._agent` to that Agent.
    - Degrades a failed `context.get_history()` to an empty list.
    """
    from agent_framework_foundry_hosting import (  # type: ignore[import-not-found]
        ResponsesHostServer,
    )

    class _ResilientResponsesHostServer(ResponsesHostServer):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__(all_agents[default_skill])
            self._all_agents = all_agents
            self._default_skill = default_skill

        async def _handle_inner_agent(self, request, context):  # type: ignore[override]
            original_get_history = context.get_history
            original_get_input_items = context.get_input_items

            _cached_items: list[Any] | None = None
            _routed_skill: str | None = None

            async def safe_get_history():  # type: ignore[no-untyped-def]
                try:
                    return await original_get_history()
                except Exception as ex:  # noqa: BLE001
                    _log.warning(
                        "context.get_history() failed (%s); proceeding with no prior history.",
                        ex,
                    )
                    return []

            async def routed_get_input_items(*args, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal _cached_items, _routed_skill
                if _cached_items is not None:
                    return _cached_items
                items = await original_get_input_items(*args, **kwargs)
                _cached_items, _routed_skill = project_router.route_input_items(items)
                return _cached_items

            # ── Pre-route ─────────────────────────────────────────────────
            try:
                raw = await original_get_input_items()
                _cached_items, _routed_skill = project_router.route_input_items(raw)
            except Exception as ex:  # noqa: BLE001
                _log.warning("pre-routing failed (%s); skill falls back to default.", ex)

            # ── Resolve skill ──────────────────────────────────────────────
            # Priority: preamble > project_log["skill"] > first-turn classifier > default.
            skill_name: str | None = _routed_skill or _resolve_project_skill()

            if skill_name is None:
                # New project / no persisted skill — classify the first message.
                candidates: list[tuple[str, str]] = []
                for n in self._all_agents:
                    if n == self._default_skill:
                        continue
                    try:
                        candidates.append((n, skill_loader.get(n).description))
                    except KeyError:
                        continue
                first_text = _first_user_text(_cached_items)
                picked = await _classify_first_turn_skill(
                    first_text, candidates, self._default_skill,
                )
                _log.info(
                    "first-turn classifier picked %r for input %r",
                    picked, first_text[:120],
                )
                if picked != self._default_skill:
                    _persist_skill_choice(picked)
                skill_name = picked

            agent = self._all_agents.get(skill_name)
            if agent is None:
                _log.warning(
                    "skill %r not found (have %s); falling back to %r.",
                    skill_name,
                    list(self._all_agents),
                    self._default_skill,
                )
                agent = self._all_agents[self._default_skill]
            self._agent = agent  # type: ignore[attr-defined]

            context.get_history = safe_get_history  # type: ignore[method-assign]
            context.get_input_items = routed_get_input_items  # type: ignore[method-assign]
            async for item in super()._handle_inner_agent(request, context):
                yield item

    return _ResilientResponsesHostServer()


def start(all_agents: dict[str, Any] | None = None) -> None:
    """Block on `ResponsesHostServer.run()` with per-request skill dispatch."""
    if all_agents is None:
        from . import foundry_host
        all_agents = foundry_host.get_all_agents()

    if _DEFAULT_SKILL not in all_agents:
        raise RuntimeError(
            f"responses_host: default skill {_DEFAULT_SKILL!r} not in agent map "
            f"(have {list(all_agents)}). Boot failed."
        )

    _build_resilient_host(all_agents, _DEFAULT_SKILL).run()
