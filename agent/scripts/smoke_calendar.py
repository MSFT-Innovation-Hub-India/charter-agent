"""One-shot smoke: ask the host Agent a calendar question and print whatever
came back — final text plus any tool-call traces. Uses the live Toolbox via
`runtime.foundry_host`. Run from the agent/ directory:

    .\.venv\Scripts\python.exe scripts/smoke_calendar.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv


def _ensure_src() -> None:
    src = Path(__file__).resolve().parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


async def main() -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    _ensure_src()

    from charter_agent.runtime import foundry_host

    foundry_host.bootstrap()

    skill_body = (
        "You are a helpful Microsoft 365 assistant for the signed-in user. "
        "You have a WorkIQ Toolbox attached with tools for Outlook mail and "
        "calendar, Teams, SharePoint, OneDrive, Word, and a Copilot search. "
        "Today is Sunday, May 17, 2026 (the user's local timezone). "
        "When the user asks about their calendar, call the calendar tools to "
        "fetch real events — do not invent any. After you have the data, "
        "summarise it concisely for the user (one short paragraph + a bullet "
        "list of events with start–end times, subject, and key attendees)."
    )

    user_prompt = "How does my calendar look tomorrow?"

    print(f">>> prompt: {user_prompt}\n")
    result = await foundry_host.run_skill(
        skill_body=skill_body,
        user_prompt=user_prompt,
    )

    text = (
        getattr(result, "text", None)
        or getattr(result, "output_text", None)
        or getattr(result, "output", None)
        or str(result)
    )
    print("=== FINAL TEXT ===")
    print(text)
    print()

    msgs = getattr(result, "messages", None)
    if msgs:
        print("=== MESSAGES (truncated) ===")
        for i, m in enumerate(msgs):
            role = getattr(m, "role", "?")
            print(f"[{i}] role={role}")
            contents = getattr(m, "contents", None) or getattr(m, "content", None)
            if isinstance(contents, list):
                for c in contents:
                    s = repr(c)
                    print(f"    {s[:400]}")
            else:
                print(f"    {repr(contents)[:400]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
