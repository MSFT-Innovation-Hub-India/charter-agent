"""Top-level invocation dispatcher: action verb → handler."""

from __future__ import annotations

import os
from typing import Any

from . import kickoff, state, workiq
from .charter import ratify as ratify_charter
from .charter.schemas import Charter
from .observability import log_activity, trace_function
from .runtime import foundry_host, skill_loader


@trace_function("charter.invocation")
async def handle_invocation(
    action: str,
    payload: dict[str, Any],
    visitor_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if action == "echo":
        return await _echo(payload, visitor_identity)
    if action == "list_tools":
        return await _list_tools()
    if action == "propose_charter":
        return await _propose_charter(payload, visitor_identity)
    if action == "ratify_charter":
        return await _ratify_charter(payload, visitor_identity)
    return {"ok": False, "action": action, "error": f"unknown action: {action}"}


@trace_function("charter.echo")
async def _echo(payload: dict[str, Any], visitor: dict[str, Any] | None) -> dict[str, Any]:
    session_id = os.environ.get("FOUNDRY_AGENT_SESSION_ID", "local")
    thread = foundry_host.get_session(session_id)
    count = state.bump_counter()
    home = state.home_dir()

    actor = (visitor or {}).get("upn", "unknown")
    log_activity(home, actor=actor, kind="echo", summary=f"echo #{count}")

    return {
        "ok": True,
        "action": "echo",
        "result": {
            "count": count,
            "session_id": session_id,
            "session_resumed": bool(thread.get("resumed")),
            "echo": payload.get("message", ""),
        },
    }


@trace_function("charter.list_tools")
async def _list_tools() -> dict[str, Any]:
    """Phase 1 smoke: enumerate the WorkIQ tools the Toolbox exposes."""
    tools = await workiq.list_available_tools()
    return {
        "ok": True,
        "action": "list_tools",
        "result": {
            "expected_servers": list(workiq.WORKIQ_SERVERS),
            "tool_count": len(tools),
            "tools": tools,
        },
    }


@trace_function("charter.propose_charter")
async def _propose_charter(
    payload: dict[str, Any], visitor: dict[str, Any] | None
) -> dict[str, Any]:
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return {
            "ok": False,
            "action": "propose_charter",
            "error": "payload.prompt (non-empty string) is required.",
        }

    skill = skill_loader.get("project-kickoff")
    session_id = os.environ.get("FOUNDRY_AGENT_SESSION_ID", "local")

    # Stamp the coordinator's UPN into the user prompt so the skill can place
    # it into stakeholders.coordinator without guessing. visitor_identity is
    # populated by the BFF from the MSAL token claims.
    coordinator_upn = (visitor or {}).get("upn", "unknown@unknown")
    framed_prompt = (
        f"[coordinator_upn: {coordinator_upn}]\n"
        f"[project_id_hint: {session_id}]\n\n"
        f"{prompt.strip()}"
    )

    run_result = await foundry_host.run_skill(
        skill_body=skill.body,
        user_prompt=framed_prompt,
        response_format=Charter,
        session_id=session_id,
    )

    proposed = _extract_charter(run_result)

    home = state.home_dir()
    log_activity(
        home,
        actor=coordinator_upn,
        kind="propose_charter",
        summary=f"proposed Charter for {proposed.project_id!r} (v{proposed.version})",
        ref=proposed.project_id,
    )

    return {
        "ok": True,
        "action": "propose_charter",
        "result": {
            "proposed_charter": proposed.model_dump(mode="json"),
        },
    }


@trace_function("charter.ratify_charter")
async def _ratify_charter(
    payload: dict[str, Any], visitor: dict[str, Any] | None
) -> dict[str, Any]:
    raw = payload.get("charter")
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "action": "ratify_charter",
            "error": "payload.charter (Charter JSON object) is required.",
        }

    try:
        proposed = Charter.model_validate(raw)
    except Exception as e:
        return {
            "ok": False,
            "action": "ratify_charter",
            "error": f"charter validation failed: {e}",
        }

    coordinator_upn = (visitor or {}).get("upn", proposed.stakeholders.coordinator)

    try:
        ratified = ratify_charter(proposed, by_upn=coordinator_upn)
    except ValueError as e:
        return {"ok": False, "action": "ratify_charter", "error": str(e)}

    log_activity(
        state.home_dir(),
        actor=coordinator_upn,
        kind="ratify_charter",
        summary=f"ratified Charter for {ratified.project_id!r} (v{ratified.version})",
        ref=ratified.project_id,
    )

    fanout_summary = await kickoff.fanout(ratified, by_upn=coordinator_upn)

    return {
        "ok": True,
        "action": "ratify_charter",
        "result": {
            "charter": ratified.model_dump(mode="json"),
            "fanout": fanout_summary,
        },
    }


def _extract_charter(run_result: Any) -> Charter:
    """Pull a Charter out of whatever shape MAF's `agent.run(...)` returned.

    `agent-framework`'s structured-output surface varies slightly across versions:
    the parsed object can live on `.value`, `.parsed`, or `.output_parsed`, and
    fall-back text on `.text` / `.content` / the final message. We try each in
    turn and validate; the explicit failure mode is more useful than a guess.
    """
    for attr in ("value", "parsed", "output_parsed"):
        candidate = getattr(run_result, attr, None)
        if isinstance(candidate, Charter):
            return candidate
        if isinstance(candidate, dict):
            return Charter.model_validate(candidate)
        if isinstance(candidate, str) and candidate.strip().startswith("{"):
            return Charter.model_validate_json(candidate)

    for attr in ("text", "content", "output_text"):
        text = getattr(run_result, attr, None)
        if isinstance(text, str) and text.strip().startswith("{"):
            return Charter.model_validate_json(text)

    raise RuntimeError(
        "propose_charter: could not extract a Charter from the agent run result "
        f"(type={type(run_result).__name__}, attrs={dir(run_result)[:20]}...)."
    )
