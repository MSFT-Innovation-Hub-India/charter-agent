"""One-shot live smoke for the propose_charter verb.

Drives the project-kickoff skill end-to-end against the real Foundry model and
the live `Charter-Agent-Tools` Toolbox. Pure read path — no SharePoint folders
are created, no emails sent, no Teams messages posted.

Run from the agent/ directory:

    .\.venv\Scripts\python.exe scripts/smoke_propose_charter.py

The prompt grounds against the bundled sample meeting-notes fixture so the
skill has something concrete to find with `workiq_ask_work_iq` /
`WorkIQCopilot___copilot_chat`. If your tenant has no such file, swap in any
description of a real project you want a Charter for.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv


def _ensure_src() -> None:
    src = Path(__file__).resolve().parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


PROMPT = (
    "We need to spin up the board pack for the May 2026 board meeting. "
    "There's a meeting-notes file in my OneDrive titled 'sample-meeting-notes' "
    "that captures the scoping discussion last week — pull it, ground the "
    "Charter in it, and propose: project id, owners, the per-section tasks "
    "with their runbook requirements, where the watch channels should listen, "
    "and where the final Word doc should land. My UPN is the coordinator. "
    "If you can't find the meeting-notes file, propose a reasonable default "
    "Charter for a board pack and flag what you assumed."
)


async def main() -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    _ensure_src()

    from charter_agent import orchestrator
    from charter_agent.runtime import foundry_host

    foundry_host.bootstrap()

    print(">>> prompt:")
    print(PROMPT)
    print()

    # Drive the verb the same way an /invocations request would.
    response = await orchestrator.handle_invocation(
        "propose_charter",
        {"prompt": PROMPT},
        visitor_identity={"upn": "sansri@microsoft.com"},
    )

    print("=== ORCHESTRATOR RESPONSE ===")
    if not response.get("ok"):
        print(json.dumps(response, indent=2))
        return 1

    charter = response["result"]["proposed_charter"]
    print(json.dumps(charter, indent=2))
    print()
    print(f"--- summary: {len(charter.get('tasks', []))} tasks, "
          f"{len(charter.get('watch_channels', []))} watch channels, "
          f"{len(charter.get('grounding_sources', []))} grounding sources ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
