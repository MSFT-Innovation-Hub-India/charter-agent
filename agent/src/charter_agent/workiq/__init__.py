"""Thin namespace over the WorkIQ MCP servers behind `Charter-Agent-Tools`.

The Toolbox is exposed as a Foundry **hosted** MCP tool (created via
`FoundryChatClient.get_mcp_tool(...)` in `runtime.foundry_host`). The Foundry
runtime owns MCP dispatch, auth, consent, and per-user identity propagation.
A client-side `connect()` + `tools/list` introspection — like the previous
raw `MCPStreamableHTTPTool` path supported — is therefore not available here.

`WORKIQ_SERVERS` below is the human-curated list of servers we expect to be
exposed behind the Toolbox (135 tools across 8 servers as of May 2026):

- WorkIQMail2       — Outlook mail (22 tools)
- WorkIQTeams       — Teams messages, channels, chats (35)
- WorkIQCalendar2   — Outlook calendar (15)
- WorkIQSharePoint2 — SharePoint sites, lists, files (35)
- WorkIQOneDrive    — OneDrive files (18)
- WorkIQWord        — Word doc read/edit (4)
- WorkIQUser        — user / org graph lookups (5)
- WorkIQCopilot     — `copilot_chat` open-ended M365 discovery (1)
"""

from __future__ import annotations

from typing import Any

WORKIQ_SERVERS: tuple[str, ...] = (
    "WorkIQMail2",
    "WorkIQTeams",
    "WorkIQCalendar2",
    "WorkIQSharePoint2",
    "WorkIQOneDrive",
    "WorkIQWord",
    "WorkIQUser",
    "WorkIQCopilot",
)


async def list_available_tools() -> list[dict[str, Any]]:
    """Return a degraded server-level summary of the WorkIQ Toolbox.

    Live per-tool catalog introspection is not available on the hosted MCP
    path — Foundry owns the MCP session. This returns the expected server
    list so callers (dashboard, `dev_run list-tools`) still have something
    structured to display.
    """
    from ..runtime import foundry_host

    # Touch the host to surface boot-time configuration errors early; the
    # returned MCP tool spec doesn't expose a tool catalogue client-side.
    foundry_host.get_toolbox()
    return [{"server": s, "name": None, "description": None} for s in WORKIQ_SERVERS]