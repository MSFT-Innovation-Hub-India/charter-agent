"""Local dev runner for the Charter Agent.

Boots the agent runtime against the *real* Foundry project (using your
`az login` identity in place of the production Managed Identity) and exposes
small commands that exercise specific pieces of the surface without going
through the Invocations server. Use it to de-risk things that are hard to
diagnose from a deployed agent — Toolbox catalogue, skill registration, an
end-to-end `propose_charter`.

Usage (from the `agent/` directory, with deps installed and `.env` populated):

    python scripts/dev_run.py list-tools
    python scripts/dev_run.py skills
    python scripts/dev_run.py propose-charter --prompt "..."
    python scripts/dev_run.py propose-charter --prompt-file path/to/prompt.txt

Reads `.env` from the agent directory (same vars listed in `.env.example`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path


def _load_env() -> None:
    """Best-effort load of `agent/.env`. Optional — env may already be set."""
    agent_dir = Path(__file__).resolve().parent.parent
    env_path = agent_dir / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _ensure_src_on_path() -> None:
    src = Path(__file__).resolve().parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


async def _cmd_list_tools(_: argparse.Namespace) -> int:
    from charter_agent import workiq
    from charter_agent.runtime import foundry_host

    foundry_host.bootstrap()
    print(
        f"connected to toolbox {os.environ.get('TOOLBOX_NAME')!r} "
        f"on {os.environ.get('FOUNDRY_PROJECT_ENDPOINT')}"
    )
    tools = await workiq.list_available_tools()
    by_server: dict[str, list[dict]] = defaultdict(list)
    for t in tools:
        by_server[t.get("server") or "<unknown>"].append(t)

    print(f"\n{len(tools)} tool(s) across {len(by_server)} server(s):\n")
    for server in sorted(by_server):
        entries = sorted(by_server[server], key=lambda x: x.get("name") or "")
        print(f"=== {server} ({len(entries)}) ===")
        for t in entries:
            name = t.get("name") or "<unnamed>"
            desc = (t.get("description") or "").strip().splitlines()
            first = desc[0] if desc else ""
            if len(first) > 90:
                first = first[:87] + "..."
            print(f"  {name:<48}  {first}")
        print()
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


async def _cmd_propose_charter(args: argparse.Namespace) -> int:
    if args.prompt and args.prompt_file:
        print("error: pass --prompt OR --prompt-file, not both", file=sys.stderr)
        return 2
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    elif args.prompt:
        prompt = args.prompt
    else:
        print("error: --prompt or --prompt-file is required", file=sys.stderr)
        return 2

    from charter_agent import orchestrator
    from charter_agent.runtime import foundry_host

    foundry_host.bootstrap()
    coordinator_upn = args.as_upn or os.environ.get("DEV_COORDINATOR_UPN", "dev@example.com")
    print(f"propose_charter as {coordinator_upn} ...\n")
    result = await orchestrator.handle_invocation(
        "propose_charter",
        {"prompt": prompt},
        visitor_identity={"upn": coordinator_upn},
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


def main() -> int:
    _load_env()
    _ensure_src_on_path()

    p = argparse.ArgumentParser(prog="dev_run", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-tools", help="enumerate the live Toolbox catalogue")
    sub.add_parser("skills", help="list skills the loader picks up")

    pc = sub.add_parser("propose-charter", help="run the propose_charter verb end-to-end")
    pc.add_argument("--prompt", help="prompt text")
    pc.add_argument("--prompt-file", help="path to a file containing the prompt")
    pc.add_argument("--as-upn", help="coordinator UPN to stamp into the invocation")

    args = p.parse_args()
    handler = {
        "list-tools": _cmd_list_tools,
        "skills": _cmd_skills,
        "propose-charter": _cmd_propose_charter,
    }[args.cmd]

    try:
        return asyncio.run(handler(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
