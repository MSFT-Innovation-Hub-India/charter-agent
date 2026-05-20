"""Top-level invocation dispatcher.

Three verbs only:

- `echo` — Phase 1 smoke. Returns a counter and the session id.
- `list_tools` — enumerates the WorkIQ Toolbox catalog, the agent-side state
  tools, and the loaded skills, for diagnostics.
- `run_skill` — load the named skill and run one host-Agent turn with the
  user's prompt. The skill body owns the workflow (what files to write,
  which WorkIQ tools to call, in what order). This module does not parse or
  validate the model's output — it returns the raw text.
"""

from __future__ import annotations

import os
from typing import Any

from . import state, workiq
from .observability import log_activity, trace_function
from .runtime import foundry_host, skill_loader
from .runtime import state_tools as _state_tools


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
    if action == "run_skill":
        return await _run_skill(payload, visitor_identity)
    return {"ok": False, "action": action, "error": f"unknown action: {action}"}


@trace_function("charter.echo")
async def _echo(payload: dict[str, Any], visitor: dict[str, Any] | None) -> dict[str, Any]:
    session_id = (visitor or {}).get("session_id") or os.environ.get(
        "FOUNDRY_AGENT_SESSION_ID", "local"
    )
    thread = foundry_host.get_session(session_id)
    count = state.bump_counter()

    actor = (visitor or {}).get("upn", "unknown")
    log_activity(state.home_dir(), actor=actor, kind="echo", summary=f"echo #{count}")

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
    tools = await workiq.list_available_tools()
    return {
        "ok": True,
        "action": "list_tools",
        "result": {
            "expected_workiq_servers": list(workiq.WORKIQ_SERVERS),
            "workiq_tool_count": len(tools),
            "workiq_tools": tools,
            "agent_side_tools": _state_tools.describe_tools(),
            "loaded_skills": [
                {"name": s.name, "description": s.description}
                for s in skill_loader.load_all()
            ],
        },
    }


@trace_function("charter.run_skill")
async def _run_skill(
    payload: dict[str, Any], visitor: dict[str, Any] | None
) -> dict[str, Any]:
    skill_name = payload.get("skill_name")
    prompt = payload.get("prompt")
    if not isinstance(skill_name, str) or not skill_name.strip():
        return {
            "ok": False,
            "action": "run_skill",
            "error": "payload.skill_name (non-empty string) is required.",
        }
    if not isinstance(prompt, str) or not prompt.strip():
        return {
            "ok": False,
            "action": "run_skill",
            "error": "payload.prompt (non-empty string) is required.",
        }

    try:
        skill = skill_loader.get(skill_name.strip())
    except KeyError as e:
        return {"ok": False, "action": "run_skill", "error": str(e)}

    session_id = (visitor or {}).get("session_id") or os.environ.get(
        "FOUNDRY_AGENT_SESSION_ID", "local"
    )
    actor = (visitor or {}).get("upn", "unknown")

    framed_prompt = (
        f"[invocation_context]\n"
        f"caller_upn: {actor}\n"
        f"session_id: {session_id}\n"
        f"[/invocation_context]\n\n"
        f"{prompt.strip()}"
    )

    log_activity(
        state.home_dir(),
        actor=actor,
        kind="run_skill_start",
        summary=f"started skill {skill.name!r}",
        ref=skill.name,
    )

    run_result = await foundry_host.run_skill(
        skill_body=skill.body,
        user_prompt=framed_prompt,
        session_id=session_id,
    )

    response_text = _extract_text(run_result)

    log_activity(
        state.home_dir(),
        actor=actor,
        kind="run_skill_end",
        summary=f"completed skill {skill.name!r} ({len(response_text)} chars)",
        ref=skill.name,
    )

    return {
        "ok": True,
        "action": "run_skill",
        "result": {
            "skill_name": skill.name,
            "session_id": session_id,
            "response_text": response_text,
        },
    }


def _extract_text(run_result: Any) -> str:
    """Best-effort pluck of the assistant's text from a MAF `Agent.run` result.

    The run-result shape varies slightly across `agent-framework` versions;
    we probe the common attributes in order. Falls back to `str(...)` so the
    caller always sees something rather than an empty payload.
    """
    for attr in ("text", "output_text", "content"):
        v = getattr(run_result, attr, None)
        if isinstance(v, str) and v:
            return v
    messages = getattr(run_result, "messages", None)
    if messages:
        last = messages[-1]
        for attr in ("text", "content"):
            v = getattr(last, attr, None)
            if isinstance(v, str) and v:
                return v
    return str(run_result)
