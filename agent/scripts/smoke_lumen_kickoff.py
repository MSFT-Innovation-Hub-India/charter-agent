"""Phase 3 live smoke: propose -> ratify -> kickoff against Project Lumen.

Grounds the kickoff prompt on a real email the coordinator just received
(the 'Project Lumen — Customer Escalation Recovery Planning' meeting notes
sent to sansri@microsoft.com). The skill must find that email via WorkIQ,
extract the workstreams + owners + due dates, and propose a Charter.

Then this script rewrites every owner_upn to the coordinator BEFORE ratifying
so the kickoff fan-out only emails the coordinator — Arvind and Vishakha are
not pinged. This exercises the full propose -> ratify -> kickoff path
end-to-end without involving real third parties.

Run from agent/:

    .\\.venv\\Scripts\\python.exe scripts/smoke_lumen_kickoff.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

COORDINATOR_UPN = "sansri@microsoft.com"


def _isolate_home() -> Path:
    """Redirect $HOME to a per-script sandbox under `agent/.smoke_home/` so
    smoke runs don't write `charter.json`/`state.json`/`activity.json` into
    the real Windows user home, and don't cross-contaminate other smoke runs.
    Must run BEFORE any import that calls `state.home_dir()`.
    """
    sandbox = Path(__file__).resolve().parent.parent / ".smoke_home" / Path(__file__).stem
    sandbox.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(sandbox)
    return sandbox

PROMPT = (
    "I just received an email in my inbox with the subject "
    "'Project Lumen — Customer Escalation Recovery Planning' "
    "(sent to me, sansri@microsoft.com). It contains the meeting notes "
    "from a scoping discussion with Arvind Raman and Vishakha Arbat "
    "about a customer escalation recovery proposal due to the customer "
    "by next Thursday 4pm. Find that email, read the notes, and propose "
    "a Charter for Project Lumen grounded in what those notes say: "
    "the workstreams, owners, due dates, runbook requirements per task, "
    "the four-section deliverable structure, and the watch channels to "
    "monitor. I am the coordinator."
)


def _ensure_src() -> None:
    src = Path(__file__).resolve().parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _rewrite_owners_to_coordinator(charter: dict) -> dict:
    """Redirect every owner_upn + the owners list to the coordinator.

    Keeps the structure of the model's proposal but ensures briefing emails
    only land in the coordinator's own mailbox. The Charter remains valid
    (single-owner, but still ratifiable).
    """
    stakeholders = charter.setdefault("stakeholders", {})
    stakeholders["coordinator"] = COORDINATOR_UPN
    stakeholders["owners"] = [COORDINATOR_UPN]
    if "deputy" in stakeholders:
        stakeholders["deputy"] = COORDINATOR_UPN
    for t in charter.get("tasks", []):
        t["owner_upn"] = COORDINATOR_UPN
    return charter


async def main() -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    sandbox = _isolate_home()
    print(f">>> sandboxed $HOME: {sandbox}")
    _ensure_src()

    from charter_agent import orchestrator
    from charter_agent.runtime import foundry_host

    foundry_host.bootstrap()

    print(">>> prompt:")
    print(PROMPT)
    print()

    # 1) Propose
    propose_resp = await orchestrator.handle_invocation(
        "propose_charter",
        {"prompt": PROMPT},
        visitor_identity={"upn": COORDINATOR_UPN},
    )
    print("=== PROPOSE RESPONSE ===")
    if not propose_resp.get("ok"):
        print(json.dumps(propose_resp, indent=2))
        return 1
    proposed = propose_resp["result"]["proposed_charter"]
    print(json.dumps(proposed, indent=2))
    print()

    # 2) Rewrite owners to the coordinator BEFORE ratify, so kickoff only
    #    emails sansri@microsoft.com.
    rewritten = _rewrite_owners_to_coordinator(proposed)
    print("=== OWNERS REWRITTEN TO COORDINATOR ===")
    print(json.dumps(rewritten["stakeholders"], indent=2))
    print("task owner_upns:",
          [t.get("owner_upn") for t in rewritten.get("tasks", [])])
    print()

    # 3) Ratify (triggers kickoff.fanout internally)
    ratify_resp = await orchestrator.handle_invocation(
        "ratify_charter",
        {"charter": rewritten},
        visitor_identity={"upn": COORDINATOR_UPN},
    )
    print("=== RATIFY + KICKOFF RESPONSE ===")
    print(json.dumps(ratify_resp, indent=2))
    if not ratify_resp.get("ok"):
        return 1

    fanout = ratify_resp["result"]["fanout"]
    print()
    print("--- kickoff summary ---")
    print(f"  sharepoint_folder : {fanout['sharepoint_folder'].get('status')}")
    print(f"  teams_kickoff     : {fanout['teams_kickoff'].get('status')}")
    print(f"  briefing_emails   : sent={fanout['briefing_emails'].get('sent')} "
          f"failures={len(fanout['briefing_emails'].get('failures', []))}")
    print(f"  outlook_tasks     : {fanout['outlook_tasks'].get('status', 'created')} "
          f"created={fanout['outlook_tasks'].get('created')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
