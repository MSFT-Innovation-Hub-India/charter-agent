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

from datetime import UTC, datetime, timedelta
from typing import Any

from agent_framework import tool  # type: ignore[import-not-found]

from ... import state
from ...observability import log_activity

_LOG_PATH = "project_log.json"
_CHARTER_PATH = "project_charter.md"

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
    if not state.exists(_LOG_PATH):
        return None
    return state.read_json(_LOG_PATH)


def _write_log(log: dict[str, Any]) -> None:
    state.write_json(_LOG_PATH, log)


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

    - `"first_run"` — no `project_log.json` in $HOME. Ground the project,
      propose tasks, then call `commit_charter` to persist both files, then
      fan out kickoffs.
    - `"resume"` — a project_log already exists. Use the returned `project_log`
      as the source of truth; do NOT re-ground or re-write the charter.

    Returns:
        `{"mode": "first_run"|"resume", "project_log": <log-or-null>,
          "charter_exists": <bool>}`.
    """
    log = _read_log()
    return {
        "mode": "resume" if log is not None else "first_run",
        "project_log": log,
        "charter_exists": state.exists(_CHARTER_PATH),
    }


@tool
def commit_charter(
    project_id: str,
    customer_name: str,
    sow_owner_upn: str,
    deputy_upn: str,
    charter_markdown: str,
    tasks: list[dict[str, Any]],
    grounding_sources: list[dict[str, Any]] | None = None,
    consolidation_section_order: list[str] | None = None,
    cross_section_checks: list[str] | None = None,
) -> dict[str, Any]:
    """Atomically write `project_charter.md` and `project_log.json` for a new project.

    Refuses if `project_log.json` already exists — use the resume-mode tools
    instead. Fills in every operational field (status enums, communication
    modes, due-date default, empty submission lists, version, timestamps) so
    the skill never has to know the on-disk shape.

    Args:
        project_id: kebab-case slug ≤64 chars (e.g. `"contoso-sow-may26"`).
        customer_name: free-form customer/RFP issuer name.
        sow_owner_upn: the coordinator's UPN.
        deputy_upn: backup coordinator's UPN. Pass an empty string if not named.
        charter_markdown: the full human-readable charter, already composed.
        tasks: one dict per SOW section. Each must contain `task_id`, `title`,
            `owner_upn`, `owner_display_name`, `is_external` (bool), and
            `runbook_requirements` (list of strings derived from the RFP).
            Optional: `due_at` (ISO; defaults to tomorrow 17:00 UTC).
            `communication_modes` is derived from `is_external` — do not pass it.
        grounding_sources: list of `{kind, ref, used, note}` dicts describing
            what the project was grounded in. Optional.
        consolidation_section_order: order of sections in the final deliverable.
            Defaults to a sensible standard SOW ordering.
        cross_section_checks: bullets describing reconciliation rules.
    """
    if state.exists(_LOG_PATH):
        return {
            "status": "error",
            "reason": "already_exists",
            "message": "project_log.json already exists; this is a resume-mode session.",
        }

    now = _now_iso()
    normalised: list[dict[str, Any]] = []
    for t in tasks:
        is_external = bool(t.get("is_external", False))
        normalised.append(
            {
                "task_id": t["task_id"],
                "title": t["title"],
                "owner_upn": t["owner_upn"],
                "owner_display_name": t.get("owner_display_name", t["owner_upn"]),
                "is_external": is_external,
                "communication_modes": _modes_for(is_external),
                "due_at": t.get("due_at") or _default_due_at(),
                "runbook_requirements": list(t.get("runbook_requirements", [])),
                "status": "assigned",
                "submissions": [],
                "kickoff_sent": {"channel": None, "ref": None, "at": None},
                "nudges": [],
                "last_polled_at": None,
            }
        )

    log: dict[str, Any] = {
        "project_id": project_id,
        "project_kind": "sow_response",
        "version": 1,
        "created_at": now,
        "sow_owner_upn": sow_owner_upn,
        "deputy_upn": deputy_upn or None,
        "customer_name": customer_name,
        "grounding_sources": list(grounding_sources or []),
        "tasks": normalised,
        "deliverable": {"format": "word"},
        "consolidation_rules": {
            "section_order": list(consolidation_section_order or [
                "executive-summary",
                "technical-scope",
                "pm-scope",
                "commercial",
                "case-studies",
            ]),
            "cross_section_checks": list(cross_section_checks or []),
        },
        "log_entries": [{"at": now, "kind": "wrote_charter", "summary": "charter committed", "ref": project_id}],
        "status": "drafted",
    }

    state.write_text(_CHARTER_PATH, charter_markdown)
    _write_log(log)
    _audit("wrote_charter", f"committed charter for {project_id} ({len(normalised)} tasks)", project_id)

    return {
        "status": "ok",
        "project_id": project_id,
        "version": 1,
        "tasks": [t["task_id"] for t in normalised],
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

    return {
        "kind": "dashboard",
        "project": log.get("project_id"),
        "customer": log.get("customer_name"),
        "status": log.get("status"),
        "summary": "",
        "due": earliest_unmet_due or "",
        "progress": {"submitted": submitted_count, "total": len(tasks)},
        "sections": sections,
        "exceptions": exceptions,
        "deliverable_url": log.get("deliverable", {}).get("url", ""),
    }


TOOLS: list[Any] = [
    load_project_state,
    commit_charter,
    record_kickoff,
    record_submission,
    mark_task_polled,
    record_nudge_sent,
    dashboard_payload,
]


def describe_tools() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for fn in TOOLS:
        name = getattr(fn, "name", None) or getattr(fn, "__name__", repr(fn))
        desc = getattr(fn, "description", None) or (getattr(fn, "__doc__", "") or "").strip()
        out.append({"name": str(name), "description": desc.split("\n", 1)[0]})
    return out


__all__ = ["TOOLS", "describe_tools"]
