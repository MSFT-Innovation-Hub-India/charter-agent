"""Domain tools for the `sow-response` skill family.

State mutation is handled by the generic `project_read_log`, `project_patch_log`,
and `project_write_log` tools in `state_tools.py`.  These two tools handle the
read-only dashboard rollup and the SSE publish step that the phase skills share.
"""

from __future__ import annotations

import json
from typing import Any

from agent_framework import tool  # type: ignore[import-not-found]

from ... import state


def _log_path() -> str:
    return state.project_path("project_log.json")


def _read_log() -> dict[str, Any] | None:
    p = _log_path()
    if not state.exists(p):
        return None
    return state.read_json(p)


@tool
def dashboard_payload() -> dict[str, Any]:
    """Return the dashboard JSON payload the UI renders from `project_log.json`.

    Call this at the end of every phase that produces user-visible output.
    The model wraps the returned dict in a ```json fenced block; the UI
    extracts the first fence.

    Returns `{"status": "no_project"}` when there is no project_log yet.
    """
    log = _read_log()
    if log is None:
        return {"status": "no_project"}

    tasks = log.get("tasks", [])
    submitted_states = {"submitted", "submitted_with_gaps"}
    section_status_map = {"in_progress": "inprogress", "overdue": "atrisk"}

    sections: list[dict[str, Any]] = []
    earliest_unmet_due: str | None = None
    exceptions: list[dict[str, Any]] = []

    for t in tasks:
        raw = t.get("status", "assigned")
        ui_status = section_status_map.get(raw, raw)
        last_signal = "awaiting reply"
        subs = t.get("submissions", [])
        if subs:
            last_signal = subs[-1].get("summary") or "reply received"
        elif t.get("kickoff_sent") and t.get("kickoff_channel"):
            last_signal = f"kickoff sent via {t.get('kickoff_channel', '')}"
        sections.append(
            {
                "task_id": t.get("task_id"),
                "title": t.get("title"),
                "owner": t.get("owner_display_name") or t.get("owner_upn"),
                "owner_upn": t.get("owner_upn"),
                "owner_oid": t.get("owner_oid"),
                "status": ui_status,
                "due_at": t.get("due_at") or "",
                "last_signal": last_signal,
            }
        )
        if raw not in submitted_states and t.get("due_at"):
            if earliest_unmet_due is None or t["due_at"] < earliest_unmet_due:
                earliest_unmet_due = t["due_at"]
        if raw == "overdue":
            exceptions.append(
                {
                    "kind": "atrisk",
                    "title": t.get("task_id"),
                    "body": f"{t.get('owner_display_name', t.get('owner_upn'))} is past due.",
                }
            )

    # Honour the section order declared in the charter if present.
    charter_order = [s.get("id") for s in log.get("charter", {}).get("sections", [])]
    if charter_order:
        idx = {tid: i for i, tid in enumerate(charter_order)}
        sections.sort(key=lambda s: idx.get(s["task_id"], len(idx)))

    submitted_count = sum(1 for t in tasks if t.get("status") in submitted_states)

    # Activity tail for the desktop client's ACTIVITY STREAM panel.
    activity_tail: list[dict[str, Any]] = []
    try:
        act_rel = state.project_path("activity.json")
        if state.exists(act_rel):
            lines = state.read_text(act_rel).splitlines()[-15:]
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                activity_tail.append(
                    {"at": obj.get("at"), "kind": obj.get("kind"), "summary": obj.get("summary")}
                )
    except Exception:  # noqa: BLE001
        activity_tail = []

    customer = log.get("customer_name") or log.get("rfp", {}).get("customer_name", "")

    return {
        "kind": "dashboard",
        "project": log.get("project_id"),
        "customer": customer,
        "skill": log.get("skill"),
        "phase": log.get("phase"),
        "status": log.get("status"),
        "summary": "",
        "due": earliest_unmet_due or "",
        "progress": {"submitted": submitted_count, "total": len(tasks)},
        "sections": sections,
        "exceptions": exceptions,
        "deliverable_url": log.get("deliverable", {}).get("url", ""),
        "activity": activity_tail,
    }


@tool
def publish_view(payload: dict[str, Any]) -> dict[str, Any]:
    """Transmit the dashboard payload to the desktop client via the SSE stream.

    Pass the exact return value of `dashboard_payload` as `payload`. The
    client reads the dashboard from this tool call's arguments rather than
    parsing the JSON fenced in the prose, keeping every field intact.
    Returns `{"ok": True}`; no on-disk side effects.
    """
    return {"ok": True}


TOOLS: list[Any] = [dashboard_payload, publish_view]


def describe_tools() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for fn in TOOLS:
        name = getattr(fn, "name", None) or getattr(fn, "__name__", repr(fn))
        desc = getattr(fn, "description", None) or (getattr(fn, "__doc__", "") or "").strip()
        out.append({"name": str(name), "description": desc.split("\n", 1)[0]})
    return out


__all__ = ["TOOLS", "describe_tools"]
