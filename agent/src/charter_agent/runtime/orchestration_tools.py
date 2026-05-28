"""In-process tool for invoking a named skill as an isolated sub-agent.

The orchestrator (`sow-response`) calls `invoke_skill(skill_name, context)` to
delegate a phase of work to the appropriate sub-skill.  Each call uses the
pre-built warm Agent for that skill — backed by the shared `FoundryChatClient`
and MCP toolbox singleton — so there is no per-call Agent construction cost.

The sub-skill runs with its own SKILL.md instructions and allowed-tools subset,
isolated from the orchestrator's conversation history.  Shared state flows
through `project_log.json`, which both the orchestrator and sub-skills read and
patch via the standard state tools.

The active project context (`state.active_project_id()`) is set by
`project_router` before the orchestrator runs and persists for the lifetime of
the request, so sub-skills inherit the correct sandbox automatically.
"""

from __future__ import annotations

from agent_framework import tool  # type: ignore[import-not-found]


@tool
async def invoke_skill(skill_name: str, context: str = "") -> str:
    """Run the named skill as an isolated sub-agent and return its text output.

    The skill runs with its own SKILL.md instructions and tool surface.
    It shares the active project context (project sandbox, project_log.json)
    with the orchestrator.  Use this to delegate a workflow phase to its
    domain-expert skill.

    Args:
        skill_name: The registered skill identifier, e.g. "sow-rfp-search".
        context: The input to pass to the sub-skill — typically the user's
            original message or a brief continuation cue.
    """
    from . import foundry_host  # lazy — avoids circular import at module load

    try:
        agent = foundry_host.get_agent(skill_name)
        result = await agent.run(messages=context or "Continue.")
        return result.text or ""
    except Exception as exc:  # noqa: BLE001
        return f"[skill-error: {skill_name} failed — {exc}]"


ORCHESTRATION_TOOLS = [invoke_skill]
