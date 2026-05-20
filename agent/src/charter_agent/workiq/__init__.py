"""Thin namespace over the WorkIQ MCP servers behind `Charter-Agent-Tools`.

The Toolbox is owned by `runtime.foundry_host` and attached to the host
`Agent` as a tool, so skills call WorkIQ operations directly through the
model's tool-loop. This module exists for two small responsibilities:

1. Enumerate the **expected** servers so the `list_tools` smoke verb can
   sanity-check the live Toolbox against the portal config.
2. Introspect the live Toolbox and return its tool catalog for diagnostics
   and the dashboard's "tools available" surface.

Live `Charter-Agent-Tools` (135 tools across 8 WorkIQ servers):
- WorkIQMail2       — Outlook mail (22 tools)
- WorkIQTeams       — Teams messages, channels, chats (35)
- WorkIQCalendar2   — Outlook calendar (15)
- WorkIQSharePoint2 — SharePoint sites, lists, files (35)
- WorkIQOneDrive    — OneDrive files (18)
- WorkIQWord        — Word doc read/edit (4)
- WorkIQUser        — user / org graph lookups (5)
- WorkIQCopilot     — `copilot_chat` open-ended M365 discovery (1)

Note: the live Toolbox does NOT currently expose an Outlook Tasks / To Do
server. Skills that need Outlook tasks must either degrade gracefully or
wait for that server to be added to the Toolbox.
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
    """Introspect the live Toolbox and return its tool catalog."""
    from ..runtime import foundry_host

    toolbox = foundry_host.get_toolbox()
    await toolbox.connect()
    return [_summarise(f) for f in toolbox.functions]


def _summarise(fn: Any) -> dict[str, Any]:
    name = getattr(fn, "name", str(fn))
    server = name.split("___", 1)[0] if "___" in name else None
    return {
        "name": name,
        "server": server,
        "description": getattr(fn, "description", None),
    }
