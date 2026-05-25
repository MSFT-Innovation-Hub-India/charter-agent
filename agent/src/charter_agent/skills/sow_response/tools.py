"""Domain tools for the `sow-response` skill.

These tools absorb the deterministic mechanics that previously lived in
SKILL.md as numbered procedures and JSON schemas: the shape of
`project_log.json`, the rules for bubbling task status up to project status,
the overdue check, the kickoff and submission idempotency guards, and the
dashboard payload shape.

Each tool returns a structured JSON-serialisable dict with a `status`
discriminator (`ok` / `noop` / `error`). Tools never compose user-facing
prose — the model writes that.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_framework import tool  # type: ignore[import-not-found]

from ... import state
from ...observability import log_activity

# The skill stamps its own name into `project_log.json` at kickoff so the
# desktop client can tag the project in its sidebar without guessing. When a
# second skill ships, it follows the same pattern (its own kickoff tool
# writes a different value here).
SKILL_NAME = "sow-response"

# Project files live under `projects/<active_project_id>/`. The desktop client
# names the active project via a context preamble on every turn; the skill
# calls `set_active_project` before any other tool so these helpers resolve
# to the right sandbox.


def _log_path() -> str:
    return state.project_path("project_log.json")


def _charter_path() -> str:
    return state.project_path("project_charter.md")

# Project-level rollup. Order matters: scan tasks once and apply the bubble rule.
_TASK_STATUSES = {
    "assigned",
    "in_progress",
    "submitted",
    "submitted_with_gaps",
    "overdue",
}
_PROJECT_STATUSES = {
    "needs_grounding",
    "drafted",
    "kicked_off",
    "in_progress",
    "submitted",
    "submitted_with_gaps",
    "closed",
}


# --- helpers --------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_due_at() -> str:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
    return tomorrow.isoformat()


def _modes_for(is_external: bool) -> dict[str, Any]:
    if is_external:
        return {
            "preferred": "email",
            "allowed": ["email"],
            "document_sharing": ["email"],
        }
    return {
        "preferred": "teams_message",
        "allowed": ["teams_message", "email"],
        "document_sharing": ["onedrive", "sharepoint", "email", "teams_message"],
    }


def _read_log() -> dict[str, Any] | None:
    p = _log_path()
    if not state.exists(p):
        return None
    return state.read_json(p)


def _write_log(log: dict[str, Any]) -> None:
    state.write_json(_log_path(), log)


def _find_task(log: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for t in log.get("tasks", []):
        if t.get("task_id") == task_id:
            return t
    return None


def _append_log_entry(log: dict[str, Any], kind: str, summary: str, ref: str | None = None) -> None:
    log.setdefault("log_entries", []).append(
        {"at": _now_iso(), "kind": kind, "summary": summary, "ref": ref}
    )


def _audit(kind: str, summary: str, ref: str | None = None) -> None:
    log_activity(state.home_dir(), actor="agent", kind=kind, summary=summary, ref=ref)


def _is_overdue(task: dict[str, Any]) -> bool:
    due = task.get("due_at")
    if not due or task.get("status") in {"submitted", "submitted_with_gaps", "closed"}:
        return False
    try:
        return datetime.fromisoformat(due) < datetime.now(UTC)
    except ValueError:
        return False


def _recompute_project_status(log: dict[str, Any]) -> str:
    tasks = log.get("tasks", [])
    if not tasks:
        return log.get("status", "drafted")
    statuses = {t.get("status", "assigned") for t in tasks}
    if statuses <= {"submitted"}:
        return "submitted"
    if statuses <= {"submitted", "submitted_with_gaps"}:
        return "submitted_with_gaps"
    if any(t.get("submissions") for t in tasks):
        return "in_progress"
    if any(t.get("kickoff_sent", {}).get("at") for t in tasks):
        return "kicked_off"
    return log.get("status", "drafted")


# --- tools ----------------------------------------------------------------


@tool
def load_project_state() -> dict[str, Any]:
    """Return the current project state so the skill can decide first-run vs resume.

    Call this once at the start of every turn, before anything else. The
    returned `mode` is the only thing you need to branch on:

    - `"first_run"` — no `project_log.json` exists yet. Call
      `stamp_project_skill` next (so the sidebar tags the project immediately),
      then ground the project via WorkIQ, then call `start_charter` and
      `add_charter_task` to persist the charter, then fan out kickoffs.
    - `"resume"` — a project_log already exists. Use the returned `project_log`
      as the source of truth; do NOT re-ground or re-write the charter.

    Returns:
        `{"mode": "first_run"|"resume", "project_log": <log-or-null>,
          "charter_exists": <bool>, "active_project_id": <str>}`.
    """
    log = _read_log()
    if log is None:
        return {
            "mode": "first_run",
            "project_log": None,
            "charter_exists": state.exists(_charter_path()),
            "active_project_id": state.active_project_id(),
        }
    return {
        "mode": "resume",
        "project_log": log,
        "charter_exists": state.exists(_charter_path()),
        "active_project_id": state.active_project_id(),
    }


@tool
def stamp_project_skill() -> dict[str, Any]:
    """Write a minimal project stub so the desktop sidebar can tag this project
    with the skill name immediately — before grounding or charter work begins.

    Call this once at the very start of first-run mode, right after
    `load_project_state` returns `mode="first_run"`. It is a no-op if a
    project log already exists (including an earlier stub from this same turn).
    `start_charter` will overwrite the stub when it commits the full charter.

    Returns `{"status": "ok"|"noop"}`.
    """
    if state.exists(_log_path()):
        return {"status": "noop", "reason": "log_already_exists"}
    stub = {
        "project_id": state.active_project_id(),
        "skill": SKILL_NAME,
        "status": "initializing",
        "tasks": [],
        "log_entries": [],
    }
    _write_log(stub)
    return {"status": "ok", "project_id": state.active_project_id(), "skill": SKILL_NAME}


@tool
def start_charter(
    project_id: str,
    customer_name: str,
    sow_owner_upn: str,
    charter_markdown: str,
    deputy_upn: str = "",
    grounding_source_kind: str = "",
    grounding_source_ref: str = "",
    grounding_source_note: str = "",
) -> dict[str, Any]:
    """Create the project: write `project_charter.md` and an empty `project_log.json`.

    Call this FIRST, then call `add_charter_task` once per SOW section, in order.
    No tasks are added by this call — the project starts with an empty `tasks` list
    and `status = "drafting"`. After all tasks are added, the project is ready
    for kickoff fan-out.

    Refuses if `project_log.json` already exists (resume mode — use the
    resume-mode tools instead).

    Args:
        project_id: kebab-case slug ≤64 chars (e.g. `"contoso-sow-may26"`).
        customer_name: free-form customer/RFP issuer name.
        sow_owner_upn: the coordinator's UPN.
        charter_markdown: the full human-readable charter, already composed.
        deputy_upn: backup coordinator's UPN. Empty string if not named.
        grounding_source_kind: e.g. `"email"`, `"meeting"`, `"copilot"`, `"file"`.
            Empty string if grounding wasn't from a single source.
        grounding_source_ref: URL or id of the grounding source. Empty if N/A.
        grounding_source_note: one-line note about what was grounded. Empty if N/A.
    """
    if state.exists(_log_path()):
        existing = _read_log() or {}
        # `load_project_state` writes a `status:"initializing"` stub on first
        # run so the desktop client can tag the sidebar with this skill's name
        # immediately. That stub is not a real charter — overwrite it.
        if existing.get("status") != "initializing":
            return {
                "status": "error",
                "reason": "already_exists",
                "message": "project_log.json already exists; this is a resume-mode session.",
            }

    now = _now_iso()
    grounding_sources: list[dict[str, Any]] = []
    if grounding_source_kind or grounding_source_ref or grounding_source_note:
        grounding_sources.append(
            {
                "kind": grounding_source_kind or "unspecified",
                "ref": grounding_source_ref,
                "used": True,
                "note": grounding_source_note,
            }
        )

    log: dict[str, Any] = {
        "project_id": project_id,
        "project_kind": "sow_response",
        "skill": SKILL_NAME,
        "version": 1,
        "created_at": now,
        "sow_owner_upn": sow_owner_upn,
        "deputy_upn": deputy_upn or None,
        "customer_name": customer_name,
        "grounding_sources": grounding_sources,
        "tasks": [],
        "deliverable": {"format": "word"},
        "consolidation_rules": {
            "section_order": [
                "executive-summary",
                "technical-scope",
                "pm-scope",
                "commercial",
                "case-studies",
            ],
            "cross_section_checks": [],
        },
        "log_entries": [
            {"at": now, "kind": "started_charter", "summary": "charter started", "ref": project_id}
        ],
        "status": "drafting",
    }

    state.write_text(_charter_path(), charter_markdown)
    _write_log(log)
    _audit("started_charter", f"started charter for {project_id}", project_id)

    return {"status": "ok", "project_id": project_id, "version": 1, "tasks": []}


@tool
def add_charter_task(
    task_id: str,
    title: str,
    owner_upn: str,
    owner_display_name: str,
    is_external: bool,
    runbook_requirements: list[str],
    due_at: str = "",
) -> dict[str, Any]:
    """Append one SOW section task to the project. Call once per section.

    Must be called AFTER `start_charter`. Each call adds exactly one task with
    `status = "assigned"`, empty submission list, and `communication_modes`
    derived from `is_external`. Idempotent on `task_id` — if a task with the
    same id exists, returns `status: "noop"`.

    When all sections have been added, the project is ready for kickoff fan-out.
    No explicit "finalize" call is needed.

    Args:
        task_id: kebab-case slug unique within the project (e.g. `"technical-scope"`).
        title: human-readable section title.
        owner_upn: UPN of the assigned owner.
        owner_display_name: friendly display name.
        is_external: True if the owner is outside the SOW Owner's tenant (different
            email domain — customer, partner, vendor). Drives the communication
            matrix: external → email-only; internal → Teams DM preferred.
        runbook_requirements: bullets derived from the RFP describing what the
            owner must deliver. Plain strings; one per requirement.
        due_at: ISO timestamp. Empty string → defaults to tomorrow 17:00 UTC.
    """
    log = _read_log()
    if log is None:
        return {
            "status": "error",
            "reason": "no_project",
            "message": "call start_charter first; no project_log.json exists.",
        }

    if _find_task(log, task_id) is not None:
        return {"status": "noop", "task_id": task_id, "reason": "task already exists"}

    task = {
        "task_id": task_id,
        "title": title,
        "owner_upn": owner_upn,
        "owner_display_name": owner_display_name or owner_upn,
        "is_external": bool(is_external),
        "communication_modes": _modes_for(bool(is_external)),
        "due_at": due_at or _default_due_at(),
        "runbook_requirements": list(runbook_requirements),
        "status": "assigned",
        "submissions": [],
        "kickoff_sent": {"channel": None, "ref": None, "at": None},
        "nudges": [],
        "last_polled_at": None,
    }
    log["tasks"].append(task)
    _append_log_entry(log, "added_task", f"added task {task_id} ({owner_upn})", task_id)
    _write_log(log)
    _audit("added_task", f"added task {task_id} for {owner_upn}", task_id)

    return {
        "status": "ok",
        "task_id": task_id,
        "task_count": len(log["tasks"]),
    }


@tool
def record_kickoff(
    task_id: str,
    channel: str,
    ref: str = "",
    failure_reason: str = "",
) -> dict[str, Any]:
    """Mark a task's kickoff as sent (or failed) and update project status.

    Idempotent — if this task already has a successful `kickoff_sent.at`,
    returns `status: "noop"` and does nothing. Call AFTER the WorkIQ
    Teams/Mail send tool returns; pass the message id / URL as `ref` if you
    have one.

    Args:
        task_id: the task whose kickoff was sent.
        channel: `"teams_message"` or `"email"`.
        ref: optional message id, URL, or other handle for the sent artifact.
        failure_reason: if the send failed, pass the error here and leave
            `ref` empty. The cursor records the failure but does not block
            other tasks.
    """
    log = _read_log()
    if log is None:
        return {"status": "error", "reason": "no_project", "message": "project_log.json does not exist."}
    task = _find_task(log, task_id)
    if task is None:
        return {"status": "error", "reason": "unknown_task", "message": f"no task {task_id!r}."}

    if task.get("kickoff_sent", {}).get("at") and not failure_reason:
        return {"status": "noop", "task_id": task_id, "reason": "already_sent"}

    now = _now_iso()
    if failure_reason:
        task["kickoff_sent"] = {
            "channel": channel,
            "ref": None,
            "at": None,
            "failure_reason": failure_reason,
        }
        _append_log_entry(log, "kickoff_failed", f"{task_id} via {channel}: {failure_reason}", task.get("owner_upn"))
        _audit("kickoff_failed", f"{task_id} via {channel}: {failure_reason}", task.get("owner_upn"))
    else:
        task["kickoff_sent"] = {"channel": channel, "ref": ref or None, "at": now}
        _append_log_entry(log, "kickoff_sent", f"{task_id} via {channel}", task.get("owner_upn"))
        _audit("kickoff_sent", f"{task_id} via {channel}", task.get("owner_upn"))

    log["status"] = _recompute_project_status(log)
    _write_log(log)
    return {
        "status": "ok",
        "task_id": task_id,
        "channel": channel,
        "project_status": log["status"],
    }


@tool
def record_submission(
    task_id: str,
    source_ref: str,
    summary: str,
    accepted: bool,
    gaps: list[str] | None = None,
) -> dict[str, Any]:
    """Record a collaborator's reply against a task and update task + project status.

    Idempotent — if `source_ref` (the message id you used as the dedup key)
    already appears in this task's submissions, returns `status: "noop"` and
    does not double-count.

    The new task status is computed for you:
    - `accepted` and `gaps` empty → `submitted`.
    - `accepted` and `gaps` non-empty → `submitted_with_gaps`.
    - `accepted=False` → `in_progress` (the reply did not address the runbook;
      surface its content as a clarifying question in the digest).
    After updating, the project status is recomputed via the bubble rule and
    `last_polled_at` is advanced.

    Args:
        task_id: the task this reply belongs to.
        source_ref: dedup key — `internetMessageId` for mail, message id for
            Teams. Must be globally unique.
        summary: one-sentence human description of the reply's content.
        accepted: True if the reply substantively addresses the runbook.
        gaps: list of runbook bullets that are still not covered. Empty list
            (or `None`) when fully covered.
    """
    log = _read_log()
    if log is None:
        return {"status": "error", "reason": "no_project"}
    task = _find_task(log, task_id)
    if task is None:
        return {"status": "error", "reason": "unknown_task", "task_id": task_id}

    for existing in task.get("submissions", []):
        if existing.get("source_ref") == source_ref:
            return {"status": "noop", "task_id": task_id, "reason": "duplicate_source_ref"}

    gap_list = list(gaps or [])
    now = _now_iso()
    task.setdefault("submissions", []).append(
        {
            "received_at": now,
            "source_ref": source_ref,
            "summary": summary,
            "accepted": accepted,
            "gaps": gap_list,
        }
    )
    if accepted and not gap_list:
        task["status"] = "submitted"
    elif accepted:
        task["status"] = "submitted_with_gaps"
    else:
        task["status"] = "in_progress"
    if _is_overdue(task):
        task["status"] = "overdue"
    task["last_polled_at"] = now

    log["status"] = _recompute_project_status(log)
    _append_log_entry(log, "captured", f"{task_id}: {summary}", source_ref)
    _audit("captured", f"{task_id}: {summary}", source_ref)
    _write_log(log)
    return {
        "status": "ok",
        "task_id": task_id,
        "task_status": task["status"],
        "project_status": log["status"],
    }


@tool
def mark_task_polled(task_id: str) -> dict[str, Any]:
    """Advance a task's `last_polled_at` to now and apply the overdue check.

    Call this after polling an owner's reply surface returns zero new
    messages — it narrows the search window for the next pass and flips the
    task to `overdue` if its due date has passed without a submission.
    """
    log = _read_log()
    if log is None:
        return {"status": "error", "reason": "no_project"}
    task = _find_task(log, task_id)
    if task is None:
        return {"status": "error", "reason": "unknown_task", "task_id": task_id}

    task["last_polled_at"] = _now_iso()
    if _is_overdue(task):
        task["status"] = "overdue"
    log["status"] = _recompute_project_status(log)
    _write_log(log)
    return {"status": "ok", "task_id": task_id, "task_status": task["status"]}


@tool
def record_nudge_sent(task_id: str, channel: str, draft_text: str, ref: str = "") -> dict[str, Any]:
    """Record that an approved follow-up nudge was sent to a task's owner.

    Use this in resume mode, AFTER the SOW Owner explicitly approves a
    drafted nudge and you send it via the appropriate WorkIQ tool. Appends
    to `tasks[].nudges[]` so subsequent polls know the cursor moved.
    """
    log = _read_log()
    if log is None:
        return {"status": "error", "reason": "no_project"}
    task = _find_task(log, task_id)
    if task is None:
        return {"status": "error", "reason": "unknown_task", "task_id": task_id}

    now = _now_iso()
    task.setdefault("nudges", []).append(
        {"at": now, "channel": channel, "ref": ref or None, "draft_text": draft_text}
    )
    _append_log_entry(log, "nudge_sent", f"{task_id} via {channel}", task.get("owner_upn"))
    _audit("nudge_sent", f"{task_id} via {channel}", task.get("owner_upn"))
    _write_log(log)
    return {"status": "ok", "task_id": task_id, "channel": channel}


@tool
def dashboard_payload() -> dict[str, Any]:
    """Return the dashboard JSON payload the UI renders from `project_log.json`.

    Call this at the end of every closing receipt (first-run) and every
    resume digest. The model wraps the returned dict in a ```json fenced
    block on a new line after the prose; the UI extracts the first fence.

    Returns `{"status": "no_project"}` when there is no project_log yet, so
    the model knows to omit the dashboard for grounding-failure replies.
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
        elif t.get("kickoff_sent", {}).get("at"):
            last_signal = f"kicked off via {t['kickoff_sent']['channel']}"
        sections.append(
            {
                "task_id": t.get("task_id"),
                "title": t.get("title"),
                "owner": t.get("owner_display_name") or t.get("owner_upn"),
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
                {"kind": "atrisk", "title": t.get("task_id"), "body": f"{t.get('owner_display_name', t.get('owner_upn'))} is past due."}
            )

    order = log.get("consolidation_rules", {}).get("section_order", [])
    if order:
        idx = {tid: i for i, tid in enumerate(order)}
        sections.sort(key=lambda s: idx.get(s["task_id"], len(idx)))

    submitted_count = sum(1 for t in tasks if t.get("status") in submitted_states)

    # Include the project's activity tail so the desktop client can render the
    # "ACTIVITY STREAM" panel in hosted mode (where it cannot reach the
    # microVM's $HOME directly). Capped small (15 entries, only at/kind/summary)
    # because the model is asked to echo the dashboard JSON verbatim and large
    # arrays get silently truncated/dropped.
    activity_tail: list[dict[str, Any]] = []
    try:
        act_path = state.home_dir() / "projects" / state.active_project_id() / "activity.json"
        if act_path.exists():
            lines = act_path.read_text(encoding="utf-8").splitlines()[-15:]
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                activity_tail.append({
                    "at": obj.get("at"),
                    "kind": obj.get("kind"),
                    "summary": obj.get("summary"),
                })
    except Exception:  # noqa: BLE001
        activity_tail = []

    return {
        "kind": "dashboard",
        "project": log.get("project_id"),
        "customer": log.get("customer_name"),
        "skill": log.get("skill"),
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
    """Transmit the dashboard payload to the desktop client.

    Call this immediately after `dashboard_payload`, passing its exact return
    value as `payload`. The client reads the dashboard from this tool call's
    arguments (which are surfaced on the SSE stream) rather than parsing the
    JSON fenced in the prose receipt — that keeps every field, including the
    activity tail, intact regardless of how the model formats its closing
    prose. Returns `{"ok": True}`; no on-disk side effects.
    """
    return {"ok": True}


TOOLS: list[Any] = [
    load_project_state,
    stamp_project_skill,
    start_charter,
    add_charter_task,
    record_kickoff,
    record_submission,
    mark_task_polled,
    record_nudge_sent,
    dashboard_payload,
    publish_view,
]


def describe_tools() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for fn in TOOLS:
        name = getattr(fn, "name", None) or getattr(fn, "__name__", repr(fn))
        desc = getattr(fn, "description", None) or (getattr(fn, "__doc__", "") or "").strip()
        out.append({"name": str(name), "description": desc.split("\n", 1)[0]})
    return out


__all__ = ["TOOLS", "describe_tools"]
