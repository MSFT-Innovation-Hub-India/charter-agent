"""Agent-side tools the host `Agent` exposes alongside the WorkIQ Toolbox.

These are the only primitives a skill needs to drive its own workflow:
- read/write text and JSON files under `$HOME` (the per-session sandbox)
- list what's there
- append entries to the human-readable activity log

The skill author decides what files exist and what shape they take. The agent
code never inspects the contents — it's project-shape-agnostic.

All paths are relative to `$HOME` and are validated against escape attempts
inside `state.py` (`..`, absolute paths → ValueError). The model can therefore
ask for any path it wants; the worst it can do is overwrite a file it itself
created.
"""

from __future__ import annotations

from typing import Any

from agent_framework import tool  # type: ignore[import-not-found]

from .. import state
from ..observability import log_activity


@tool
def state_write_text(path: str, content: str) -> str:
    """Atomically write a text file under the session's `$HOME` directory.

    Use for prose artifacts the skill produces: project charters in Markdown,
    drafted emails, notes, transcripts. Parent directories are created as
    needed. Existing files are overwritten.

    Args:
        path: Relative path under `$HOME` (e.g. "project_charter.md",
            "drafts/nudge-001.md"). Absolute paths and ".." escapes are rejected.
        content: The text content to write.
    """
    p = state.write_text(path, content)
    return f"wrote {p.relative_to(state.home_dir()).as_posix()} ({len(content)} chars)"


@tool
def state_read_text(path: str) -> str:
    """Read a text file from the session's `$HOME` directory.

    Args:
        path: Relative path under `$HOME`.
    """
    return state.read_text(path)


@tool
def state_write_json(path: str, obj: dict[str, Any]) -> str:
    """Atomically write a JSON file under the session's `$HOME` directory.

    Use for structured state the skill needs to load back deterministically
    (without re-asking the LLM to parse it): the project log, per-task status,
    cursors, anything you'll want to read with code.

    Args:
        path: Relative path under `$HOME` (e.g. "project_log.json").
        obj: Any JSON-serialisable object. Pretty-printed, UTF-8.
    """
    p = state.write_json(path, obj)
    return f"wrote {p.relative_to(state.home_dir()).as_posix()}"


@tool
def state_read_json(path: str) -> dict[str, Any]:
    """Read a JSON file from the session's `$HOME` directory.

    Args:
        path: Relative path under `$HOME`.
    """
    return state.read_json(path)


@tool
def state_list_files(path: str = ".") -> list[str]:
    """List file paths (relative to `$HOME`) under the given directory.

    Returns an empty list if the directory does not exist.

    Args:
        path: Relative directory path under `$HOME`. Defaults to the root.
    """
    return state.list_files(path)


@tool
def state_file_exists(path: str) -> bool:
    """Return True if the given relative path exists under `$HOME`."""
    return state.exists(path)


@tool
def log_workflow_step(kind: str, summary: str, ref: str = "") -> str:
    """Append an entry to the human-readable workflow log (`$HOME/activity.json`).

    Call this after EVERY material step the skill performs — e.g. after
    grounding the kickoff prompt, after writing the project charter, after
    sending a briefing email, after detecting a collaborator's submission.
    Each entry becomes one line of NDJSON that the dashboard renders as the
    project's running narrative.

    Args:
        kind: A short snake_case identifier for what happened
            (e.g. "grounded_kickoff", "wrote_charter", "sent_briefing_email").
        summary: A one-sentence human description of the step.
        ref: Optional reference (URL, file path, message id) for the artifact
            this step touched. Pass an empty string when not applicable.
    """
    log_activity(
        state.home_dir(),
        actor="agent",
        kind=kind,
        summary=summary,
        ref=ref or None,
    )
    return "logged"


STATE_TOOLS: list[Any] = [
    state_write_text,
    state_read_text,
    state_write_json,
    state_read_json,
    state_list_files,
    state_file_exists,
    log_workflow_step,
]


def describe_tools() -> list[dict[str, str]]:
    """Catalog the agent-side tools for `list_tools`/debugging output."""
    out: list[dict[str, str]] = []
    for fn in STATE_TOOLS:
        name = getattr(fn, "name", None) or getattr(fn, "__name__", repr(fn))
        desc = getattr(fn, "description", None) or (getattr(fn, "__doc__", "") or "").strip()
        out.append({"name": str(name), "description": desc.split("\n", 1)[0]})
    return out


__all__ = ["STATE_TOOLS", "describe_tools"]
