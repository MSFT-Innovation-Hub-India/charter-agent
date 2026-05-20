r"""Live smoke for the sow-response skill's resume branch (SKILL.md §1 → §8 → §9).

Two modes:

    # Seed a synthetic post-kickoff $HOME, then run the resume invocation.
    .\.venv\Scripts\python.exe scripts/smoke_resume.py --seed

    # Re-run resume against the already-seeded $HOME (no overwrite).
    .\.venv\Scripts\python.exe scripts/smoke_resume.py

`--seed` writes a `project_log.json` + `project_charter.md` to
`agent/.smoke_home/smoke_resume/` representing a Northwind SOW kickoff that
went out N days ago (default 7). All tasks are owned by the test UPN
(default `sansri@microsoft.com`) so when §9 polls Mail/Teams it has a real
inbox to search against. With nothing pre-replied the digest should still
come back with "no new submissions" for each task; if you reply to one of
the kickoff threads in the meantime, the next run should classify it and
flip that task's status.

Run from `agent/`:
    .\.venv\Scripts\python.exe scripts/smoke_resume.py --seed
    # ... optionally reply to a kickoff email in your inbox ...
    .\.venv\Scripts\python.exe scripts/smoke_resume.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv


SMOKE_HOME = Path(__file__).resolve().parent.parent / ".smoke_home" / "smoke_resume"
FALLBACK_OWNER = "sansri@microsoft.com"  # used only if DEV_COORDINATOR_UPN is unset in env / .env
DEFAULT_KICKOFF_DAYS_AGO = 7
PROJECT_ID = "northwind-sow-smoke"
CUSTOMER = "Northwind Traders"


def _isolate_home() -> None:
    SMOKE_HOME.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(SMOKE_HOME)
    # Intentionally NOT overriding USERPROFILE: the Azure CLI reads its login
    # cache from %USERPROFILE%\.azure on Windows, and DefaultAzureCredential
    # invokes `az` as a subprocess. state.home_dir() reads HOME first, so
    # overriding HOME alone is enough to redirect $HOME-scoped state files.


def _ensure_src() -> None:
    src = Path(__file__).resolve().parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso_n_days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat(timespec="seconds")


def _build_seed(owner_upn: str, kickoff_days_ago: int) -> tuple[str, dict]:
    """Return (charter_markdown, project_log_dict)."""
    kickoff_at = _iso_n_days_ago(kickoff_days_ago)
    due_at = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec="seconds")

    charter_md = f"""# Project Charter — {CUSTOMER} SOW Response

- Project ID: {PROJECT_ID}
- SOW Owner: {owner_upn}
- Customer: {CUSTOMER}
- Kickoff sent: {kickoff_at}
- Due to customer: {due_at}

## Stakeholders
- SOW Owner: {owner_upn}
- Internal collaborators (single-user smoke; everyone is the SOW Owner): {owner_upn}

## Sections / tasks
- technical-scope — owner: {owner_upn}
- pm-scope — owner: {owner_upn}
- commercial — owner: {owner_upn}
- case-studies — owner: {owner_upn}

## Deliverable
- Format: Word (.docx)
- The final SOW response document is assembled at consolidation time.
  Collaborators reply on whatever surface suits them; no shared folder
  or template was pre-created at kickoff.
"""

    def _task(task_id: str, title: str, requirements: list[str]) -> dict:
        return {
            "task_id": task_id,
            "title": title,
            "owner_upn": owner_upn,
            "is_external": False,
            "communication_modes": {
                "preferred": "email",
                "allowed": ["email", "teams_message"],
                "document_sharing": ["email", "onedrive", "sharepoint", "teams_message"],
            },
            "due_at": due_at,
            "runbook_requirements": requirements,
            "status": "in_progress",
            "submissions": [],
            "kickoff_sent": {
                "channel": "email",
                "ref": None,
                "at": kickoff_at,
            },
            "last_polled_at": None,
        }

    project_log = {
        "project_id": PROJECT_ID,
        "customer": CUSTOMER,
        "sow_owner_upn": owner_upn,
        "created_at": kickoff_at,
        "tasks": [
            _task(
                "technical-scope",
                "Technical scope draft",
                [
                    "Reference architecture diagram with named Azure components",
                    "Data flows between components, including ingress/egress",
                    "Non-functional requirements (latency, availability, throughput)",
                    "Assumptions and exclusions",
                ],
            ),
            _task(
                "pm-scope",
                "Project management scope and WBS",
                [
                    "Work breakdown structure with phases and milestones",
                    "Effort estimate per role and phase (in hours)",
                    "RACI matrix for delivery team and customer",
                    "Risk register with top 5 risks and mitigations",
                ],
            ),
            _task(
                "commercial",
                "Commercial proposal",
                [
                    "Pricing model (fixed vs T&M) with rationale",
                    "Payment milestones tied to deliverables",
                    "Effort hours per role aligned to PM WBS",
                    "Validity period and assumptions",
                ],
            ),
            _task(
                "case-studies",
                "Relevant case studies",
                [
                    "At least 2 case studies from the same industry vertical",
                    "Each case study: customer name (or anonymised), problem, solution, outcome metric",
                    "Customer reference contact (if approved for sharing)",
                ],
            ),
        ],
        "consolidation_rules": {
            "section_order": [
                "executive-summary",
                "technical-scope",
                "pm-scope",
                "commercial",
                "case-studies",
            ],
            "cross_section_checks": [
                "Effort hours in commercial section sum equals PM WBS effort sum, per role and per phase",
                "Every component named in technical scope appears in both the PM WBS and the commercial pricing breakdown",
                "Every commercial payment milestone corresponds to a deliverable in the technical or PM scope",
            ],
        },
        "deliverable": {"format": "word"},
        "log_entries": [
            {"at": kickoff_at, "kind": "grounded", "summary": "synthetic seed", "ref": PROJECT_ID},
            {"at": kickoff_at, "kind": "wrote_charter", "summary": "seed charter", "ref": PROJECT_ID},
            {"at": kickoff_at, "kind": "kickoff_sent", "summary": f"kickoff to {owner_upn} via email", "ref": owner_upn},
        ],
        "status": "kicked_off",
    }
    return charter_md, project_log


def _seed_home(owner_upn: str, kickoff_days_ago: int) -> None:
    from charter_agent import state

    charter_md, project_log = _build_seed(owner_upn, kickoff_days_ago)
    state.write_text("project_charter.md", charter_md)
    state.write_json("project_log.json", project_log)
    print(f">>> seeded $HOME at {SMOKE_HOME}")
    print(f"    project_charter.md  ({len(charter_md)} chars)")
    print(f"    project_log.json    ({len(project_log['tasks'])} tasks, kickoff {kickoff_days_ago}d ago)")
    print()


def _dump_state_snapshot(label: str) -> None:
    from charter_agent import state

    print(f"=== STATE SNAPSHOT: {label} ===")
    try:
        plog = state.read_json("project_log.json")
    except FileNotFoundError:
        print("  (no project_log.json yet)")
        return
    print(f"  project_log.status = {plog.get('status')!r}")
    for task in plog.get("tasks", []):
        print(
            f"  - {task['task_id']}: status={task['status']!r} "
            f"submissions={len(task.get('submissions', []))} "
            f"last_polled_at={task.get('last_polled_at')}"
        )
    print()


async def _run(owner_upn: str) -> int:
    from charter_agent.orchestrator import handle_invocation
    from charter_agent.runtime import foundry_host

    foundry_host.bootstrap()

    _dump_state_snapshot("BEFORE")

    prompt = (
        f"What's the status of the {CUSTOMER} SOW response? "
        "Please poll for any new replies from collaborators since you last "
        "checked, update the project log, and give me the current digest "
        "with any recommended next actions."
    )

    print(f">>> action=run_skill skill=sow-response")
    print(f">>> prompt: {prompt}\n")

    result = await handle_invocation(
        action="run_skill",
        payload={"skill_name": "sow-response", "prompt": prompt},
        visitor_identity={"upn": owner_upn, "session_id": PROJECT_ID},
    )

    print("=== ENVELOPE ===")
    print(json.dumps({k: v for k, v in result.items() if k != "result"}, indent=2))
    print()

    inner = result.get("result", {})
    response_text = inner.get("response_text") or ""
    print("=== RESPONSE TEXT ===")
    print(response_text)
    print()

    _dump_state_snapshot("AFTER")
    return 0 if result.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true", help="(re)write a synthetic post-kickoff project_log.json before invoking.")
    parser.add_argument("--owner", default=None, help="UPN for all task owners in the seed. Default: $DEV_COORDINATOR_UPN, else sansri@microsoft.com.")
    parser.add_argument(
        "--kickoff-days-ago",
        type=int,
        default=DEFAULT_KICKOFF_DAYS_AGO,
        help=f"How many days ago to backdate the seeded kickoff (default: {DEFAULT_KICKOFF_DAYS_AGO}).",
    )
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    _isolate_home()
    _ensure_src()

    owner = args.owner or os.environ.get("DEV_COORDINATOR_UPN", FALLBACK_OWNER)

    if args.seed:
        _seed_home(owner, args.kickoff_days_ago)

    return asyncio.run(_run(owner))


if __name__ == "__main__":
    raise SystemExit(main())
