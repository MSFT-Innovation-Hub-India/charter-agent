"""Responses-protocol boot path.

Wraps the set of warm per-skill Agents in a `ResponsesHostServer` that
dispatches each request to the correct Agent based on the active project's
persisted skill identity.

Routing logic (per-request, in `_handle_inner_agent`):
  1. Pre-route: eagerly call `context.get_input_items()`, run
     `project_router.route_input_items()` so `state.active_project_id()` is
     set before we read the project log. Cache the result so the wrapped getter
     returns it unchanged when the Agent calls it later.
  2. Resolve skill: preamble `skill=` field > `project_log["skill"]` > default.
  3. Swap `self._agent` to the warm Agent for that skill before calling
     `super()._handle_inner_agent()`, which uses `self._agent` internally.

This keeps exactly one MAF runtime (invariant 12): all skills share the same
`FoundryChatClient` and `MCPStreamableHTTPTool`; only the `instructions` +
in-process `tools` differ between per-skill Agents.

Also applies two resilience patches from the canonical foundry-toolbox sample:
  - `context.get_history()` failure → degrade to empty history instead of 500.
  - `context.get_input_items()` → project routing side-effect before model runs.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import state
from . import project_router

_DEFAULT_SKILL = "sow-response"
_log = logging.getLogger(__name__)


def _resolve_project_skill(default: str) -> str:
    """Read the skill name from the active project's log, or return `default`.

    Called after `project_router.route_input_items()` has set the active
    project so `state.project_path()` resolves to the right sandbox.
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
    return default


def _build_resilient_host(all_agents: dict[str, Any], default_skill: str) -> Any:
    """Wrap `ResponsesHostServer` with per-request skill dispatch and history
    resilience.

    On every request:
    - Pre-routes the input to set the active project sandbox.
    - Swaps `self._agent` to the warm Agent for the resolved skill.
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

            # Cache for the pre-routed items so the wrapped getter returns them
            # unchanged — avoids double-consuming a non-idempotent source.
            _cached_items: list[Any] | None = None

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
                nonlocal _cached_items
                if _cached_items is not None:
                    return _cached_items
                items = await original_get_input_items(*args, **kwargs)
                _cached_items = project_router.route_input_items(items)
                return _cached_items

            # ── Pre-route ─────────────────────────────────────────────────
            # Eagerly consume and route the input before the Agent is invoked
            # so that (a) active_project_id is set, and (b) we can read the
            # project log to resolve the skill for this request.
            try:
                raw = await original_get_input_items()
                _cached_items = project_router.route_input_items(raw)
            except Exception as ex:  # noqa: BLE001
                _log.warning("pre-routing failed (%s); skill falls back to default.", ex)

            # ── Resolve skill ──────────────────────────────────────────────
            # Priority: preamble field > project_log["skill"] > default.
            skill_name = (
                project_router.last_routed_skill()
                or _resolve_project_skill(self._default_skill)
            )
            agent = self._all_agents.get(skill_name)
            if agent is None:
                _log.warning(
                    "skill %r not found (have %s); falling back to %r.",
                    skill_name,
                    list(self._all_agents),
                    self._default_skill,
                )
                agent = self._all_agents[self._default_skill]
            # Swap the agent before super() uses self._agent internally.
            self._agent = agent  # type: ignore[attr-defined]

            context.get_history = safe_get_history  # type: ignore[method-assign]
            context.get_input_items = routed_get_input_items  # type: ignore[method-assign]
            async for item in super()._handle_inner_agent(request, context):
                yield item

    return _ResilientResponsesHostServer()


def start(all_agents: dict[str, Any] | None = None) -> None:
    """Block on `ResponsesHostServer.run()` with per-request skill dispatch.

    `all_agents` is the dict of warm per-skill Agents from
    `foundry_host.get_all_agents()`. When called without arguments (e.g.
    from a smoke script) it falls back to importing the host directly.
    """
    if all_agents is None:
        from . import foundry_host
        all_agents = foundry_host.get_all_agents()

    if _DEFAULT_SKILL not in all_agents:
        raise RuntimeError(
            f"responses_host: default skill {_DEFAULT_SKILL!r} not in agent map "
            f"(have {list(all_agents)}). Boot failed."
        )

    _build_resilient_host(all_agents, _DEFAULT_SKILL).run()
