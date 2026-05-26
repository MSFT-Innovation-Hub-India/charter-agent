"""Host-level project routing.

Each user message from the desktop client is prefixed with a one-line context
preamble that names the active project and optionally the active skill, e.g.

    [charter-agent-context: project_id=p-9a3f12 is_new=false skill=sow-response]
    <the user's actual message>

This module parses that line out of the incoming Responses input *before* the
model is invoked, calls `state.set_active_project_id()` deterministically, and
strips the preamble so the skill body never sees it. The mechanism is generic
and skill-agnostic: any future skill running on this host inherits the same
per-project sandbox without knowing project switching exists.

The parsed `skill` name (if present) is returned as the second element of
the tuple from `route_input_items()`. The host stores it in a per-request
local variable and uses it to dispatch to the correct warm Agent.

See `AGENTS.md` invariants 1, 2, 6, 12: project switching is a cross-cutting
concern, not a workflow step inside any one skill.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .. import state
from ..observability import log_activity

_log = logging.getLogger(__name__)

_PREAMBLE_RE = re.compile(
    r"^\s*\[charter-agent-context:\s*"
    r"project_id=(?P<pid>[A-Za-z0-9_\-]{1,64})"
    r"(?:\s+is_new=(?P<new>true|false))?"
    r"(?:\s+skill=(?P<skill>[A-Za-z0-9_\-]{1,64}))?"
    r"\s*\]\s*\n?",
    re.IGNORECASE,
)


def _strip_preamble_text(text: str) -> tuple[str, str | None, bool, str | None]:
    """Return `(stripped_text, project_id_or_None, is_new, skill_or_None)`."""
    m = _PREAMBLE_RE.match(text or "")
    if not m:
        return text, None, False, None
    pid = m.group("pid")
    is_new = (m.group("new") or "false").lower() == "true"
    skill = m.group("skill") or None
    return text[m.end():], pid, is_new, skill


def _apply(pid: str, is_new: bool) -> None:
    try:
        state.set_active_project_id(pid)
    except ValueError as e:
        _log.warning("project_router: rejected project_id %r (%s); leaving active project unchanged.", pid, e)
        return
    log_activity(
        actor="host",
        kind="project.switch",
        summary=f"active project = {pid}" + (" (new)" if is_new else ""),
    )


def route_input_items(items: Any) -> tuple[Any, str | None]:
    """Strip the preamble from the first text-bearing input item, set the active
    project, and return ``(items, skill_or_None)``.

    Returning the skill name directly (instead of stashing it in a module
    global) makes concurrent requests safe — each caller holds its own
    reference and there is no shared mutable state.

    Handles three shapes seen in `agent_framework_foundry_hosting`:
      - `items` is a plain string (the original ``request.input``).
      - `items` is a list of ``Item`` Pydantic models with ``.content`` blocks.
      - `items` is a list of plain dicts mirroring the same shape.
    """
    if isinstance(items, str):
        new_text, pid, is_new, skill = _strip_preamble_text(items)
        if pid:
            _apply(pid, is_new)
            return new_text, skill
        return items, None

    if not items:
        return items, None

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
            if not isinstance(text, str):
                continue
            new_text, pid, is_new, skill = _strip_preamble_text(text)
            if pid is None:
                continue
            _apply(pid, is_new)
            if hasattr(blk, "text"):
                try:
                    setattr(blk, "text", new_text)
                except Exception:  # frozen model — fall back to dict mutation
                    pass
            elif isinstance(blk, dict):
                blk["text"] = new_text
            return items, skill
        # only inspect the first content-bearing item
        return items, None
    return items, None
