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

from . import foundry_host
from . import project_router

_DEFAULT_SKILL = "sow-response"
_log = logging.getLogger(__name__)


def _build_resilient_host(agent: Any) -> Any:
    """Wrap `ResponsesHostServer` so a transient `context.get_history()` failure
    degrades to "no prior turns" instead of `server_error`, and so the
    incoming user message is routed to the right project sandbox via
    `project_router` before the model sees it.

    Workaround for an alpha bug in `agent_framework_foundry_hosting` documented
    in the canonical `04-foundry-toolbox` sample.
    """
    from agent_framework_foundry_hosting import (  # type: ignore[import-not-found]
        ResponsesHostServer,
    )

    class _ResilientResponsesHostServer(ResponsesHostServer):  # type: ignore[misc]
        async def _handle_inner_agent(self, request, context):  # type: ignore[override]
            original_get_history = context.get_history
            original_get_input_items = context.get_input_items

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
                items = await original_get_input_items(*args, **kwargs)
                return project_router.route_input_items(items)

            context.get_history = safe_get_history  # type: ignore[method-assign]
            context.get_input_items = routed_get_input_items  # type: ignore[method-assign]
            async for item in super()._handle_inner_agent(request, context):
                yield item

    return _ResilientResponsesHostServer(agent)


def build_main_agent(skill_name: str = _DEFAULT_SKILL) -> Any:
    """Return the warm per-skill Agent built by `foundry_host.bootstrap()`.

    The Agent is constructed once at boot from the skill's `SkillBundle`
    (manifest body + in-process tools + filtered toolbox). The Responses
    host owns conversation history via `previous_response_id`; the Agent
    was built with `default_options={"store": False}` accordingly.
    """
    return foundry_host.get_agent(skill_name)


def start(agent: Any | None = None) -> None:
    """Block on `ResponsesHostServer.run()` with the configured agent."""
    if agent is None:
        agent = build_main_agent()
    _build_resilient_host(agent).run()
