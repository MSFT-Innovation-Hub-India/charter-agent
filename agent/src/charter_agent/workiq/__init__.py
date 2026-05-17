"""Thin typed surface over the WorkIQ MCP servers behind `Charter-Agent-Tools`.

The Toolbox is owned by `runtime.foundry_host`; this module is the sole legitimate
caller of toolbox tools from anywhere else in the codebase. Per-tool wrappers land
in Phase 2 as the kickoff flow needs them — for now this module just enumerates the
servers that are in scope and exposes a `list_available_tools()` helper that
introspects the live Toolbox via MAF.

Servers in `Charter-Agent-Tools` (confirmed live, v1 — 135 tools across 8 servers):
- WorkIQMail2       — Outlook mail (22 tools)
- WorkIQTeams       — Teams messages, channels, chats (35)
- WorkIQCalendar2   — Outlook calendar (15)
- WorkIQSharePoint2 — SharePoint sites, lists, files (35)
- WorkIQOneDrive    — OneDrive files (18)
- WorkIQWord        — Word doc read/edit (4)
- WorkIQUser        — user / org graph lookups (5)
- WorkIQCopilot     — `copilot_chat` open-ended M365 discovery (1)

Note: the live Toolbox does NOT currently expose an Outlook Tasks / To Do server.
The kickoff skill that needs Outlook tasks must either degrade gracefully or wait
for that server to be added to the Toolbox.
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
    """Introspect the live Toolbox and return its tool catalog.

    Phase 1 smoke verb: proves the Toolbox connection is healthy and that the
    expected WorkIQ servers are reachable. Uses the `MCPStreamableHTTPTool`
    contract (`connect()` then iterate the `functions` property).
    """
    from ..runtime import foundry_host

    toolbox = foundry_host.get_toolbox()
    await toolbox.connect()
    return [_summarise(f) for f in toolbox.functions]


def _summarise(fn: Any) -> dict[str, Any]:
    # MAF AIFunction.name preserves the live MCP tool name, which the Foundry
    # Toolbox formats as `WorkIQ<Server>___<ToolName>` (triple underscore is the
    # server/operation separator).
    name = getattr(fn, "name", str(fn))
    server = name.split("___", 1)[0] if "___" in name else None
    return {
        "name": name,
        "server": server,
        "description": getattr(fn, "description", None),
    }


# ---------------------------------------------------------------------------
# Typed wrappers — deterministic side-effects for the kickoff fan-out.
#
# We don't hard-code WorkIQ tool names: each server exposes slightly different
# operation names (e.g. `workiq_mail_send` vs `send_mail` vs `WorkIQMail2_send`)
# and the Toolbox sanitises `.`/`-` to `_`. So each wrapper carries a list of
# candidate names and `resolve_tool` picks the first one the live catalog
# actually exposes (caching the choice). If none match we fail loudly with a
# message that includes the candidates and a sample of what the Toolbox does
# expose, so the fix is obvious from the logs.
# ---------------------------------------------------------------------------

_tool_name_cache: dict[str, str] = {}


async def resolve_tool(operation: str, candidates: list[str]) -> str:
    """Return the first tool name from `candidates` that the Toolbox actually
    exposes; cache the choice under `operation` for subsequent calls.

    Matching is case-insensitive and tries both exact and substring match (some
    Toolboxes prefix tool names with the server namespace).
    """
    if operation in _tool_name_cache:
        return _tool_name_cache[operation]

    catalog = await list_available_tools()
    names = [str(t.get("name", "")) for t in catalog]
    names_ci = {n.lower(): n for n in names if n}

    for c in candidates:
        cl = c.lower()
        if cl in names_ci:
            _tool_name_cache[operation] = names_ci[cl]
            return names_ci[cl]
        for n_lower, n_real in names_ci.items():
            if cl in n_lower or n_lower.endswith("_" + cl) or n_lower.endswith(cl):
                _tool_name_cache[operation] = n_real
                return n_real

    raise RuntimeError(
        f"workiq.resolve_tool: no Toolbox tool matched operation {operation!r}; "
        f"tried candidates {candidates!r}. First 20 toolbox tools: {names[:20]}"
    )


async def _call(operation: str, candidates: list[str], args: dict[str, Any]) -> Any:
    from ..runtime import foundry_host

    tool_name = await resolve_tool(operation, candidates)
    return await foundry_host.call_toolbox_tool(tool_name, args)


# --- WorkIQMail2 ----------------------------------------------------------


async def send_mail(
    *, to: list[str], subject: str, body: str, body_format: str = "html"
) -> Any:
    """Send an email immediately (no draft step)."""
    return await _call(
        "send_mail",
        ["WorkIQMail2___SendEmailWithAttachments"],
        {
            "to": to,
            "subject": subject,
            "body": body,
            "body_format": body_format,
        },
    )


# --- WorkIQTeams ----------------------------------------------------------


async def post_teams_message(*, team_id: str, channel_id: str, body: str) -> Any:
    return await _call(
        "post_teams_message",
        ["WorkIQTeams___SendMessageToChannel"],
        {"team_id": team_id, "channel_id": channel_id, "body": body},
    )


async def send_teams_dm(*, recipient_upn: str, body: str) -> Any:
    """Direct-message a user by UPN (no need to look up chatId first)."""
    return await _call(
        "send_teams_dm",
        ["WorkIQTeams___SendMessageToUser"],
        {"recipient": recipient_upn, "body": body},
    )


# --- WorkIQSharePoint2 ----------------------------------------------------


async def create_sharepoint_folder(
    *, site_id: str, drive_id: str, parent_path: str, folder_name: str
) -> Any:
    return await _call(
        "create_sharepoint_folder",
        ["WorkIQSharePoint2___createFolder"],
        {
            "site_id": site_id,
            "drive_id": drive_id,
            "parent_path": parent_path,
            "folder_name": folder_name,
        },
    )


# --- WorkIQCopilot --------------------------------------------------------


async def copilot_chat(*, prompt: str) -> Any:
    """Open-ended grounding query against M365 (docs, emails, chats, meetings).

    Used by the project-kickoff skill (AGENTS.md §11.3) for the "ground the
    kickoff prompt" phase before drilling into specific files with typed tools.
    """
    return await _call(
        "copilot_chat",
        ["WorkIQCopilot___copilot_chat"],
        {"prompt": prompt},
    )


# --- Outlook Tasks --------------------------------------------------------
# The current `Charter-Agent-Tools` Toolbox does NOT expose a Tasks/To Do
# server. Calling this wrapper will raise — skills that need Outlook tasks
# should detect the absence and fall back to a SharePoint list or a Teams
# post until a Tasks server is added to the Toolbox.


async def create_outlook_task(
    *, owner_upn: str, title: str, body: str, due_at: str | None = None
) -> Any:
    raise NotImplementedError(
        "Outlook Tasks (Microsoft To Do) is not exposed by the live "
        "`Charter-Agent-Tools` Toolbox. Add a Tasks-capable WorkIQ server to "
        "the Toolbox, or have the kickoff skill use a SharePoint list / Teams "
        "post as the assignment surface."
    )
