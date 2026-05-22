"""Local dev runner for the Charter Agent.

Boots the agent runtime against the *real* Foundry project (using your
`az login` identity in place of the production Managed Identity) and exposes
small commands that exercise specific pieces of the surface without going
through the Responses server. Use it to de-risk things that are hard to
diagnose from a deployed agent — Toolbox catalogue, skill registration.

Usage (from the `agent/` directory, with deps installed and `.env` populated):

    python scripts/dev_run.py list-tools
    python scripts/dev_run.py skills

Reads `.env` from the agent directory (same vars listed in `.env.example`).
For an end-to-end Responses-protocol smoke, run `scripts/smoke_responses.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


def _ensure_src_on_path() -> None:
    src = Path(__file__).resolve().parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


async def _cmd_list_tools(_: argparse.Namespace) -> int:
    from charter_agent import workiq
    from charter_agent.runtime import foundry_host

    foundry_host.bootstrap()
    print(
        f"configured toolbox {os.environ.get('TOOLBOX_NAME')!r} "
        f"on {os.environ.get('FOUNDRY_PROJECT_ENDPOINT')}"
    )
    print(
        "\nnote: the Toolbox is now exposed as a Foundry hosted MCP tool; "
        "per-tool catalog introspection happens server-side, not from this "
        "client. listing the expected server set instead.\n"
    )
    tools = await workiq.list_available_tools()
    for t in sorted(tools, key=lambda x: x["server"] or ""):
        print(f"  {t['server']}")
    return 0


async def _cmd_skills(_: argparse.Namespace) -> int:
    from charter_agent.runtime import skill_loader

    skills = skill_loader.load_all()
    print(f"loaded {len(skills)} skill(s) from {skill_loader.skills_dir()}:\n")
    for s in sorted(skills, key=lambda x: x.name):
        desc = s.description.strip().replace("\n", " ")
        if len(desc) > 110:
            desc = desc[:107] + "..."
        tools = ",".join(s.allowed_tools) or "-"
        print(f"  {s.name:<22}  tools={tools:<32}  {desc}")
    return 0


def main() -> int:
    _load_env()
    _ensure_src_on_path()

    p = argparse.ArgumentParser(prog="dev_run", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-tools", help="enumerate the live Toolbox catalogue")
    sub.add_parser("skills", help="list skills the loader picks up")

    args = p.parse_args()
    handler = {
        "list-tools": _cmd_list_tools,
        "skills": _cmd_skills,
    }[args.cmd]

    try:
        return asyncio.run(handler(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
