"""Local SSE smoke for the Responses-protocol boot path.

Assumes the agent is already running locally, e.g.:

    cd agent
    python -m charter_agent

Then in another shell:

    python scripts/smoke_responses.py "Hi, what skills do you have?"

POSTs to `http://localhost:8088/responses?stream=true` and prints SSE frames
as they arrive. Multi-turn: pass `--previous-response-id <id>` to continue
the conversation thread the host server is tracking.
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
    p.add_argument("--no-stream", action="store_true", help="request JSON instead of SSE")
    args = p.parse_args()

    body: dict[str, object] = {"input": args.prompt}
    if args.previous_response_id:
        body["previous_response_id"] = args.previous_response_id
    if not args.no_stream:
        body["stream"] = True

    if args.no_stream:
        r = httpx.post(args.url, json=body, timeout=120.0)
        print(f"HTTP {r.status_code}")
        print(json.dumps(r.json(), indent=2))
        return 0 if r.is_success else 1

    print(f"POST {args.url}?stream=true  body={json.dumps(body)}")
    with httpx.stream("POST", args.url, json=body, timeout=120.0) as r:
        print(f"HTTP {r.status_code}")
        if not r.is_success:
            print(r.read().decode("utf-8", errors="replace"))
            return 1
        for line in r.iter_lines():
            if line:
                print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
