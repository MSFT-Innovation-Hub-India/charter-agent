"""Local SSE smoke for the Responses-protocol boot path.

Assumes the agent is already running locally, e.g.:

    cd agent
    python -m charter_agent

Then in another shell:

    python scripts/smoke_responses.py "Hi, what skills do you have?"

POSTs to `http://localhost:8088/responses?stream=true` and prints SSE frames
as they arrive. Multi-turn: pass `--previous-response-id <id>` to continue
the conversation thread the host server is tracking.

Session binding (AGENTS.md \u00a711.7 + Foundry hosted-sessions docs): by default
this script does NOT send `agent_session_id` -- the platform creates a fresh
sandbox per call and echoes the id back on `response.created` /
`response.completed`; the script prints it as `AGENT_SESSION_ID: <id>` at the
end. To hit the same per-session `$HOME` on a follow-up call (e.g. to drive
SKILL.md \u00a71 mode-detect into resume mode), pass `--session-id <id>` (also
stamped as the `x-agent-chat-isolation-key` header).
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

DEFAULT_URL = "http://localhost:8088/responses"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("prompt", help="user message to send")
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--previous-response-id", default=None)
    p.add_argument(
        "--session-id",
        default=None,
        help=(
            "client-supplied agent_session_id. If unset, the platform creates "
            "a fresh sandbox and echoes the id back on response.created / "
            "response.completed; this script prints it. Reuse the same value "
            "across turns to hit the same per-session $HOME (AGENTS.md \u00a711.7)."
        ),
    )
    p.add_argument("--no-stream", action="store_true", help="request JSON instead of SSE")
    args = p.parse_args()

    body: dict[str, object] = {"input": args.prompt}
    if args.session_id:
        body["agent_session_id"] = args.session_id
    if args.previous_response_id:
        body["previous_response_id"] = args.previous_response_id
    if not args.no_stream:
        body["stream"] = True

    headers: dict[str, str] = {}
    if args.session_id:
        headers["x-agent-chat-isolation-key"] = args.session_id

    if args.no_stream:
        r = httpx.post(args.url, json=body, headers=headers or None, timeout=120.0)
        print(f"HTTP {r.status_code}")
        payload = r.json() if r.is_success else None
        if payload is not None:
            print(json.dumps(payload, indent=2))
            sid = payload.get("agent_session_id") or (payload.get("response") or {}).get("agent_session_id")
            if sid:
                print(f"\nAGENT_SESSION_ID: {sid}")
        else:
            print(r.text)
        return 0 if r.is_success else 1

    print(f"POST {args.url}?stream=true  body={json.dumps(body)}")
    returned_session_id: str | None = None
    with httpx.stream("POST", args.url, json=body, headers=headers or None, timeout=120.0) as r:
        print(f"HTTP {r.status_code}")
        if not r.is_success:
            print(r.read().decode("utf-8", errors="replace"))
            return 1
        for line in r.iter_lines():
            if not line:
                continue
            print(line)
            if line.startswith("data:"):
                try:
                    obj = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                resp = obj.get("response") if isinstance(obj, dict) else None
                sid = (resp or {}).get("agent_session_id") if isinstance(resp, dict) else None
                if sid:
                    returned_session_id = sid
    if returned_session_id:
        print(f"\nAGENT_SESSION_ID: {returned_session_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
