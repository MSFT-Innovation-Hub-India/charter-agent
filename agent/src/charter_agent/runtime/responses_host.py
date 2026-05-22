"""Responses-protocol boot path: wrap the warm Foundry chat client in a MAF
`Agent` with the `sow-response` skill body as instructions, attach the WorkIQ
Toolbox and agent-side state tools, and hand it to `ResponsesHostServer`.

The MAF runtime emits the full OpenAI Responses SSE event stream automatically
(`response.created` → `response.output_text.delta` → `response.completed`,
plus tool-call events). No event bookkeeping here.

Per AGENTS.md invariant 12 there is still exactly one runtime; this module is
an alternate *boot path* on top of the same warm `FoundryChatClient` +
`MCPStreamableHTTPTool` that `foundry_host.bootstrap()` already wires.
"""

from __future__ import annotations

import logging
from typing import Any

from . import foundry_host, skill_loader
from .state_tools import STATE_TOOLS

_DEFAULT_SKILL = "sow-response"
_log = logging.getLogger(__name__)


def _build_resilient_host(agent: Any) -> Any:
    """Wrap `ResponsesHostServer` so a transient `context.get_history()` failure
    degrades to "no prior turns" instead of `server_error`.

    Workaround for an alpha bug in `agent_framework_foundry_hosting` documented
    in the canonical `04-foundry-toolbox` sample.
    """
    from agent_framework_foundry_hosting import (  # type: ignore[import-not-found]
        ResponsesHostServer,
    )

    class _ResilientResponsesHostServer(ResponsesHostServer):  # type: ignore[misc]
        async def _handle_inner_agent(self, request, context):  # type: ignore[override]
            original_get_history = context.get_history

            async def safe_get_history():  # type: ignore[no-untyped-def]
                try:
                    return await original_get_history()
                except Exception as ex:  # noqa: BLE001
                    _log.warning(
                        "context.get_history() failed (%s); proceeding with no prior history.",
                        ex,
                    )
                    return []

            context.get_history = safe_get_history  # type: ignore[method-assign]
            async for item in super()._handle_inner_agent(request, context):
                yield item

    return _ResilientResponsesHostServer(agent)


def build_main_agent(skill_name: str = _DEFAULT_SKILL) -> Any:
    """Construct the singleton `Agent` baked with `skill_name`'s body.

    `default_options={"store": False}` because the Responses host manages
    conversation history itself via `previous_response_id`; we don't ask the
    upstream model to persist it.
    """
    from agent_framework import Agent  # type: ignore[import-not-found]

    skill_loader.load_all()
    skill = skill_loader.get(skill_name)
    chat_client = foundry_host.get_chat_agent()
    toolbox = foundry_host.get_toolbox()

    return Agent(
        client=chat_client,
        instructions=skill.body,
        tools=[toolbox, *STATE_TOOLS],
        default_options={"store": False},
    )


def start(agent: Any | None = None) -> None:
    """Block on `ResponsesHostServer.run()` with the configured agent."""
    if agent is None:
        agent = build_main_agent()
    _build_resilient_host(agent).run()
