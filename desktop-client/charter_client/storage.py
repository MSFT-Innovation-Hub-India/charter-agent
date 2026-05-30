"""Client-side persistence and agent-sandbox readers.

Owns the projects store (per-mode), per-project transcripts, the dashboard
view cache, the append-only session-history log, and the disk-derived view
builders that let the UI repaint without a model turn.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from .config import (
    _AGENT_HOME,
    _PROJECTS_PATH,
    _SESSION_LOG_PATH,
    _TRANSCRIPT_DIR,
    _TRANSCRIPT_MAX_TURNS,
    _VIEW_CACHE_PATH,
    logger,
)


def _transcript_path(mode: str, pid: str) -> pathlib.Path:
    return _TRANSCRIPT_DIR / f"{mode}-{pid}.json"


def _load_transcript(mode: str, pid: str) -> list[dict[str, Any]]:
    p = _transcript_path(mode, pid)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _save_transcript_turn(mode: str, pid: str, user_text: str, agent_text: str) -> None:
    _TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    p = _transcript_path(mode, pid)
    try:
        existing: list[dict[str, Any]] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except Exception:  # noqa: BLE001
        existing = []
    existing.append({"role": "user", "text": user_text})
    if agent_text:
        existing.append({"role": "agent", "text": agent_text})
    # Trim to cap: keep the most recent N turn-pairs (2 messages each).
    max_msgs = _TRANSCRIPT_MAX_TURNS * 2
    if len(existing) > max_msgs:
        existing = existing[-max_msgs:]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _load_view_cache() -> dict[str, Any]:
    if not _VIEW_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_VIEW_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_view_cache(cache: dict[str, Any]) -> None:
    _VIEW_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _VIEW_CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    tmp.replace(_VIEW_CACHE_PATH)


def _gen_project_id() -> str:
    import uuid
    return f"p-{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


_seen_sessions: set[str] = set()  # in-process dedup so we don't write duplicates in one run


def _log_session(mode: str, project_id: str, session_id: str) -> None:
    """Append a new session_id to the session history log (NDJSON, append-only).

    This is the only place that records session IDs durably. projects.json only
    keeps the LATEST session per project and gets overwritten on forks — this log
    survives that and lets you recover the original session ID to file a Foundry bug.
    """
    if not session_id or session_id in _seen_sessions:
        return
    _seen_sessions.add(session_id)
    try:
        _SESSION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({
            "at": _now_iso(),
            "mode": mode,
            "project_id": project_id,
            "session_id": session_id,
        }, ensure_ascii=False)
        with _SESSION_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(entry + "\n")
        logger.debug("[session-log] recorded session=%s project=%s", session_id, project_id)
    except Exception as ex:  # noqa: BLE001
        logger.debug("[session-log] write failed: %s", ex)


_MODES = ("local", "hosted")


def _empty_projects_store() -> dict[str, Any]:
    return {
        "active": {m: None for m in _MODES},
        "projects": {m: {} for m in _MODES},
    }


def _migrate_projects_store(data: dict[str, Any]) -> dict[str, Any]:
    """Promote a pre-per-mode flat store to the per-mode shape.

    Old shape: {"active": "p-xxx", "projects": {"p-xxx": {...}}}
    New shape: {"active": {"local": "p-xxx", "hosted": null}, "projects": {"local": {...}, "hosted": {}}}
    Existing projects are bucketed under `local` since that's where dev workflows live.
    """
    projects = data.get("projects") or {}
    active = data.get("active")
    if isinstance(active, dict) and isinstance(projects, dict) and "local" in projects and "hosted" in projects:
        # Already per-mode; just ensure all keys present.
        for m in _MODES:
            projects.setdefault(m, {})
            active.setdefault(m, None)
        return {"active": active, "projects": projects}
    # Flat → per-mode migration.
    return {
        "active": {"local": active if isinstance(active, str) else None, "hosted": None},
        "projects": {"local": projects if isinstance(projects, dict) else {}, "hosted": {}},
    }


def _load_projects() -> dict[str, Any]:
    if not _PROJECTS_PATH.exists():
        return _empty_projects_store()
    try:
        raw = json.loads(_PROJECTS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return _empty_projects_store()
    return _migrate_projects_store(raw)


def _save_projects(data: dict[str, Any]) -> None:
    _PROJECTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PROJECTS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(_PROJECTS_PATH)


# ---------- agent-sandbox readers (state-from-disk; no model turn) ----------

def _read_project_log(pid: str) -> dict[str, Any] | None:
    """Read `<AGENT_HOME>/projects/<pid>/project_log.json` from disk."""
    p = _AGENT_HOME / "projects" / pid / "project_log.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _dashboard_from_log(log: dict[str, Any]) -> dict[str, Any]:
    """Mirror of `dashboard_payload` tool: derive UI dashboard from project_log.

    Kept here so the client can repaint without a model turn. The shape must
    match what the agent's `dashboard_payload()` tool returns — see
    `agent/src/charter_agent/skills/sow_response/tools.py`.
    """
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
        else:
            ks = t.get("kickoff_sent")
            if isinstance(ks, dict) and ks.get("at"):
                last_signal = f"kicked off via {ks.get('channel', 'unknown')}"
            elif ks is True:
                last_signal = "kicked off"
        sections.append({
            "task_id": t.get("task_id"),
            "title": t.get("title"),
            "owner": t.get("owner_display_name") or t.get("owner_upn"),
            "owner_upn": t.get("owner_upn"),
            "owner_oid": t.get("owner_oid"),
            "status": ui_status,
            "due_at": t.get("due_at") or "",
            "last_signal": last_signal,
        })
        if raw not in submitted_states and t.get("due_at"):
            if earliest_unmet_due is None or t["due_at"] < earliest_unmet_due:
                earliest_unmet_due = t["due_at"]
        if raw == "overdue":
            exceptions.append({
                "kind": "atrisk",
                "title": t.get("task_id"),
                "body": f"{t.get('owner_display_name', t.get('owner_upn'))} is past due.",
            })
    charter_order = [s.get("id") for s in (log.get("charter") or {}).get("sections", [])]
    if charter_order:
        idx = {tid: i for i, tid in enumerate(charter_order)}
        sections.sort(key=lambda s: idx.get(s["task_id"], len(idx)))
    submitted = sum(1 for t in tasks if t.get("status") in submitted_states)
    customer = log.get("customer_name") or (log.get("rfp") or {}).get("customer_name", "")
    return {
        "kind": "dashboard",
        "project": log.get("project_id"),
        "customer": customer,
        "skill": log.get("skill"),
        "phase": log.get("phase"),
        "status": log.get("status"),
        "summary": "",
        "due": earliest_unmet_due or "",
        "progress": {"submitted": submitted, "total": len(tasks)},
        "sections": sections,
        "exceptions": exceptions,
        "deliverable_url": (log.get("deliverable") or {}).get("url", ""),
    }


def _read_activity_tail(pid: str, limit: int = 200) -> list[dict[str, Any]]:
    """Return the last `limit` rows from the project's activity.json (NDJSON).

    Per-project file lives at `<AGENT_HOME>/projects/<pid>/activity.json`.
    The agent writes there via `observability.log_activity`, which scopes
    every entry to the currently-active project.
    """
    p = _AGENT_HOME / "projects" / pid / "activity.json"
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


def _project_view(pid: str, *, mode: str | None = None) -> dict[str, Any]:
    """Build the view for a project (dashboard + recent audit).

    Prefers the agent's on-disk state under `<AGENT_HOME>/projects/<pid>/`,
    which is the source of truth in local mode. For hosted mode the agent's
    `$HOME` lives in a Foundry microVM we can't read directly, so we fall
    back to the client-side `view_cache.json` populated from the most recent
    `turn.complete` dashboard payload.

    The activity tail is per-project: in local mode we read
    `<AGENT_HOME>/projects/<pid>/activity.json` directly; in hosted mode we
    never read the local disk (it would surface entries from unrelated
    local-mode runs) and rely on the cache only.
    """
    log = _read_project_log(pid)
    dashboard = _dashboard_from_log(log) if log else None
    activity = _read_activity_tail(pid) if mode == "local" else []
    transcript = _load_transcript(mode, pid) if mode else []
    if dashboard is None and mode:
        cache = _load_view_cache().get(f"{mode}/{pid}") or {}
        cached_dash = cache.get("dashboard")
        if cached_dash:
            # Tag the cached payload so the UI can show a "stale" indicator
            # and so the boot handler can decide to auto-refresh.
            cached_dash = {**cached_dash, "from_cache": True, "saved_at": cache.get("saved_at") or ""}
            dashboard = cached_dash
        if not activity:
            activity = cache.get("activity") or []
    return {
        "project_id": pid,
        "dashboard": dashboard,
        "activity": activity,
        "transcript": transcript,
    }


def _diff_sections(
    prev_snap: dict[str, str],
    new_sections: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Return sections whose status changed since prev_snap.

    prev_snap maps task_id → previous status string.
    Each returned dict: {task_id, title, old_status, new_status}.
    Only reports tasks that were already tracked (no spurious first-run noise).
    """
    changes = []
    for sec in new_sections:
        tid = sec.get("task_id") or ""
        new_st = sec.get("status") or ""
        old_st = prev_snap.get(tid)
        if old_st is not None and old_st != new_st:
            changes.append({
                "task_id": tid,
                "title": sec.get("title") or tid,
                "old_status": old_st,
                "new_status": new_st,
            })
    return changes
