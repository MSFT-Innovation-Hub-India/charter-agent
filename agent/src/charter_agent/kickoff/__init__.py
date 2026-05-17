"""Kickoff fan-out: deterministic side-effects after Charter ratification.

The chartering reasoning lives in the `project-kickoff` skill. Once the Charter
is ratified, the fan-out is just plumbing — create a SharePoint folder, post a
Teams kickoff message, send briefing emails, create Outlook tasks. So it's
plain code (per §4.4 decision rule), not another LLM round-trip per side-effect.

Idempotency: every step is recorded under `state.kickoff` so a re-run skips
work that already succeeded. This matters because Foundry may replay an
invocation on transient failure, and the coordinator may legitimately re-run
the ratify verb after editing the Charter.
"""

from __future__ import annotations

from typing import Any

from .. import state, workiq
from ..charter.schemas import Charter, Task
from ..observability import log_activity, trace_function


@trace_function("charter.kickoff.fanout")
async def fanout(charter: Charter, *, by_upn: str) -> dict[str, Any]:
    s = state.read_state()
    kickoff = s.setdefault("kickoff", {})

    summary = {
        "sharepoint_folder": await _maybe_sharepoint(charter, kickoff),
        "teams_kickoff": await _maybe_teams(charter, kickoff),
        "briefing_emails": await _maybe_briefing_emails(charter, kickoff),
        "outlook_tasks": await _maybe_outlook_tasks(charter, kickoff),
    }

    state.write_state(s)
    log_activity(
        state.home_dir(),
        actor=by_upn,
        kind="kickoff",
        summary=(
            f"fanout for {charter.project_id!r}: "
            f"folder={summary['sharepoint_folder']['status']}, "
            f"teams={summary['teams_kickoff']['status']}, "
            f"emails={summary['briefing_emails']['sent']}, "
            f"tasks={summary['outlook_tasks']['created']}"
        ),
        ref=charter.project_id,
    )
    return summary


async def _maybe_sharepoint(charter: Charter, kickoff: dict[str, Any]) -> dict[str, Any]:
    if kickoff.get("sharepoint_folder_done"):
        return {"status": "already_done"}
    if charter.deliverable.format not in {"word", "excel", "pdf", "markdown"}:
        return {"status": "skipped"}

    site_url, folder_path = _split_sharepoint_location(charter.deliverable.output_location)
    if site_url is None:
        return {"status": "skipped", "reason": "output_location is not a SharePoint URL"}

    await workiq.create_sharepoint_folder(site_url=site_url, folder_path=folder_path)
    kickoff["sharepoint_folder_done"] = True
    kickoff["sharepoint_folder_path"] = charter.deliverable.output_location
    return {"status": "created", "path": charter.deliverable.output_location}


async def _maybe_teams(charter: Charter, kickoff: dict[str, Any]) -> dict[str, Any]:
    if kickoff.get("teams_kickoff_done"):
        return {"status": "already_done"}

    teams_channel = next(
        (c for c in charter.watch_channels if c.kind == "teams_channel"), None
    )
    if teams_channel is None:
        return {"status": "skipped", "reason": "no teams_channel watcher in Charter"}

    team_id = teams_channel.config.get("team_id")
    channel_id = teams_channel.config.get("channel_id")
    if not team_id or not channel_id:
        return {"status": "skipped", "reason": "teams_channel watcher missing team_id/channel_id"}

    body = _render_kickoff_message(charter)
    await workiq.post_teams_message(team_id=team_id, channel_id=channel_id, body=body)
    kickoff["teams_kickoff_done"] = True
    return {"status": "posted", "team_id": team_id, "channel_id": channel_id}


async def _maybe_briefing_emails(charter: Charter, kickoff: dict[str, Any]) -> dict[str, Any]:
    sent: list[str] = list(kickoff.setdefault("briefing_emails_sent", []))
    failures: list[dict[str, Any]] = []
    new_sent = 0

    for owner in charter.stakeholders.owners:
        if owner in sent:
            continue
        owned_tasks = [t for t in charter.tasks if t.owner_upn == owner]
        if not owned_tasks:
            continue
        try:
            await workiq.send_mail(
                to=[owner],
                subject=f"[{charter.project_id}] You've been assigned tasks",
                body=_render_briefing_email(charter, owner, owned_tasks),
            )
        except Exception as e:  # surface to caller; idempotency preserved
            failures.append({"owner": owner, "error": str(e)})
            continue
        sent.append(owner)
        new_sent += 1

    kickoff["briefing_emails_sent"] = sent
    return {"sent": new_sent, "total": len(sent), "failures": failures}


async def _maybe_outlook_tasks(charter: Charter, kickoff: dict[str, Any]) -> dict[str, Any]:
    created: list[str] = list(kickoff.setdefault("outlook_tasks_created", []))
    failures: list[dict[str, Any]] = []
    new_created = 0

    for t in charter.tasks:
        if t.task_id in created:
            continue
        try:
            await workiq.create_outlook_task(
                owner_upn=t.owner_upn,
                title=f"[{charter.project_id}] {t.title}",
                body=_render_task_body(t),
                due_at=t.due_at.isoformat() if t.due_at else None,
            )
        except Exception as e:
            failures.append({"task_id": t.task_id, "error": str(e)})
            continue
        created.append(t.task_id)
        new_created += 1

    kickoff["outlook_tasks_created"] = created
    return {"created": new_created, "total": len(created), "failures": failures}


# ---------------------------------------------------------------------------
# Rendering helpers — kept deliberately plain. If we want LLM-personalised
# briefing emails later, the `draft-outbound` skill is the place; this stays
# template-only so the fan-out has no model dependency.
# ---------------------------------------------------------------------------


def _render_kickoff_message(c: Charter) -> str:
    lines = [
        f"<p><b>Kicking off {c.project_kind.replace('_', ' ')}: {c.project_id}</b></p>",
        f"<p>Coordinator: {c.stakeholders.coordinator}</p>",
        "<p>Owners and tasks:</p>",
        "<ul>",
    ]
    for t in c.tasks:
        due = f" — due {t.due_at.date().isoformat()}" if t.due_at else ""
        lines.append(f"<li><b>{t.owner_upn}</b>: {t.title}{due}</li>")
    lines.append("</ul>")
    lines.append(f"<p>Deliverable: {c.deliverable.output_location} ({c.deliverable.format}).</p>")
    return "".join(lines)


def _render_briefing_email(c: Charter, owner: str, tasks: list[Task]) -> str:
    lines = [
        f"<p>Hi,</p>",
        f"<p>You've been assigned the following on <b>{c.project_id}</b> "
        f"({c.project_kind.replace('_', ' ')}):</p>",
        "<ul>",
    ]
    for t in tasks:
        due = f" — due {t.due_at.date().isoformat()}" if t.due_at else ""
        lines.append(f"<li><b>{t.title}</b>{due}")
        if t.description:
            lines.append(f"<br/>{t.description}")
        if t.runbook_requirements:
            lines.append("<ul>")
            for r in t.runbook_requirements:
                lines.append(f"<li>{r}</li>")
            lines.append("</ul>")
        lines.append("</li>")
    lines.append("</ul>")
    lines.append(
        f"<p>Final artifact goes to <a href=\"{c.deliverable.output_location}\">"
        f"{c.deliverable.output_location}</a>.</p>"
    )
    lines.append(f"<p>— {c.stakeholders.coordinator} (via the charter agent)</p>")
    return "".join(lines)


def _render_task_body(t: Task) -> str:
    parts = [t.description] if t.description else []
    if t.runbook_requirements:
        parts.append("Requirements:\n- " + "\n- ".join(t.runbook_requirements))
    return "\n\n".join(parts) if parts else t.title


def _split_sharepoint_location(location: str) -> tuple[str | None, str]:
    """Split `/sites/board/2026-05` style paths into (site_url, folder_path).

    For Phase 2b we treat the whole `output_location` as folder_path under the
    tenant root, and return None for site_url so the wrapper picks a sensible
    default. A more sophisticated split (e.g. parsing full SharePoint URLs)
    lands in Phase 4 when capture handlers need it.
    """
    if location.startswith("/sites/"):
        parts = location.split("/", 4)  # ['', 'sites', '<site>', '<path...>']
        if len(parts) >= 4:
            site = parts[2]
            sub = "/" + "/".join(parts[3:]) if len(parts) > 3 else "/"
            return site, sub
    if location.startswith("https://"):
        return location, "/"
    return None, location


__all__ = ["fanout"]
