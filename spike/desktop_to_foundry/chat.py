"""Generic one-shot desktop client for the deployed charter-agent.

A sibling of `calendar_today.py` whose only difference is that the prompt is a
CLI argument instead of a hardcoded calendar question. Use it to drive any
workflow the agent supports — SOW kickoff, resume / status, ad-hoc M365 query.

Same machinery as `calendar_today.py`:
  - Acquires a user bearer via `az login` (AzureCliCredential) so the Foundry
    platform's OAuth Identity Passthrough propagates your identity into every
    WorkIQ MCP call (you see your own M365 data, not the agent MI's).
  - Streams the SSE event sequence, prints tool calls + text deltas.
  - Captures `agent_session_id` from the response.created / response.completed
    payload and prints it so you can re-bind on a follow-up turn.
  - Handles the `oauth_consent_request` event by opening the consent URL in a
    browser, waiting for you to consent, and resuming with `previous_response_id`.

Example — kick off the SOW response for the Northwind RFP:

    $env:AGENT_RESPONSES_URL = "https://<your-deployed-agent>/responses"
    az login
    python spike/desktop_to_foundry/chat.py "I just had a Teams meeting about the Northwind RFP. Pull the meeting notes and the RFP email, then kick off the SOW response."

Then on a follow-up turn (resume mode, same sandbox), pass the captured id:

    python spike/desktop_to_foundry/chat.py --session-id <captured-id> "what's the status of the Northwind SOW?"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from typing import Any

import httpx
from azure.identity import AzureCliCredential

AGENT_RESPONSES_URL = os.environ.get("AGENT_RESPONSES_URL", "").strip()
TENANT_ID = os.environ.get("SPIKE_TENANT_ID") or None
SCOPE = "https://ai.azure.com/.default"
HTTP_TIMEOUT = httpx.Timeout(600.0, connect=30.0)


def fail(msg: str) -> None:
    print(f"\n!! {msg}", file=sys.stderr)
    sys.exit(1)


def acquire_user_token() -> str:
    print(f"\n[auth] acquiring token via az login for {SCOPE}")
    if TENANT_ID:
        print(f"[auth] tenant={TENANT_ID}")
    cred = AzureCliCredential(tenant_id=TENANT_ID) if TENANT_ID else AzureCliCredential()
    try:
        token = cred.get_token(SCOPE)
    except Exception as e:  # noqa: BLE001
        fail(f"AzureCliCredential failed: {e}\nDid you run `az login`?")
    return token.token  # type: ignore[possibly-unbound]


def iter_sse_events(response: httpx.Response):
    event_name: str | None = None
    data_lines: list[str] = []
    for raw in response.iter_lines():
        line = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                try:
                    parsed: Any = json.loads(payload)
                except json.JSONDecodeError:
                    parsed = {"raw": payload}
                yield {"event": event_name or (parsed.get("type") if isinstance(parsed, dict) else "message"), "data": parsed}
            event_name = None
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())


def call_agent(
    token: str,
    prompt: str,
    *,
    previous_response_id: str | None,
    agent_session_id: str | None,
) -> tuple[str | None, str | None, str | None, dict | None]:
    """POST one /responses turn, stream SSE, print events.

    Returns: (response_id, agent_session_id, completed_text, consent_payload).
    """
    body: dict[str, Any] = {"input": prompt, "stream": True, "store": False}
    if agent_session_id:
        body["agent_session_id"] = agent_session_id
    if previous_response_id:
        body["previous_response_id"] = previous_response_id

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if agent_session_id:
        headers["x-agent-chat-isolation-key"] = agent_session_id

    response_id: str | None = None
    returned_session_id: str | None = None
    completed_text: str | None = None
    consent_payload: dict | None = None
    tool_calls: list[dict[str, str]] = []

    print(f"\n[call] POST {AGENT_RESPONSES_URL}")
    if agent_session_id:
        print(f"[call] agent_session_id={agent_session_id!r}  (sent in body + header)")
    else:
        print("[call] no agent_session_id sent; platform will create one")
    if previous_response_id:
        print(f"[call] resuming previous_response_id={previous_response_id}")
    print(f"[call] prompt: {prompt!r}\n")

    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        with client.stream("POST", AGENT_RESPONSES_URL, json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                err = resp.read().decode("utf-8", errors="replace")
                fail(f"HTTP {resp.status_code}: {err[:2000]}")
            for evt in iter_sse_events(resp):
                data = evt["data"]
                etype = data.get("type") if isinstance(data, dict) else None
                tag = etype or evt["event"]

                if not isinstance(data, dict):
                    continue

                if tag == "response.created":
                    resp_obj = data.get("response") or {}
                    response_id = resp_obj.get("id") or data.get("id")
                    returned_session_id = (
                        resp_obj.get("agent_session_id")
                        or data.get("agent_session_id")
                        or returned_session_id
                    )
                    print(f"  \u2190 response.created (response_id={response_id})")
                    continue

                if tag == "response.output_item.added":
                    item = data.get("item") or {}
                    if item.get("type") in ("function_call", "tool_call"):
                        tool_calls.append({"name": str(item.get("name") or "?"), "args": ""})
                        print(f"  \u2192 tool call: {item.get('name')}")
                    continue

                if tag == "response.function_call_arguments.delta":
                    if tool_calls:
                        tool_calls[-1]["args"] += str(data.get("delta") or "")
                    continue

                if tag == "response.function_call_arguments.done":
                    if tool_calls:
                        tool_calls[-1]["args_final"] = str(data.get("arguments") or "")
                        # Truncate noisy arg payloads for readability.
                        preview = (tool_calls[-1].get("args_final") or "")[:400]
                        print(f"     args: {preview}")
                    continue

                if tag == "response.output_text.delta":
                    delta = data.get("delta") or ""
                    if delta:
                        print(delta, end="", flush=True)
                    continue

                if tag == "response.completed":
                    print()  # newline before the terminal payload
                    resp_obj = data.get("response") or {}
                    returned_session_id = (
                        resp_obj.get("agent_session_id")
                        or data.get("agent_session_id")
                        or returned_session_id
                    )
                    out = resp_obj.get("output") or []
                    text_parts: list[str] = []
                    for item in out:
                        for c in (item.get("content") or []):
                            if c.get("type") in ("output_text", "text") and c.get("text"):
                                text_parts.append(c["text"])
                    if text_parts:
                        completed_text = "\n".join(text_parts)
                    print(f"  \u2190 response.completed (status={resp_obj.get('status')})")
                    continue

                if tag in ("response.failed", "error", "response.error") or (
                    isinstance(etype, str) and (etype.endswith(".failed") or etype.endswith(".error"))
                ):
                    print(f"  \u2190 {tag}\n     payload: {json.dumps(data, indent=2)[:3000]}")
                    continue

                if (
                    "oauth_consent" in tag.lower()
                    or "consent" in (data.get("type") or "").lower()
                    or "consent_url" in json.dumps(data).lower()
                ):
                    consent_payload = data
                    print(f"  \u2190 {tag}\n     consent payload: {json.dumps(data, indent=2)[:1500]}")
                    continue

    print()
    if returned_session_id:
        print(f"[call] server reports agent_session_id={returned_session_id!r}")
    return response_id, returned_session_id, completed_text, consent_payload


def extract_consent_url(payload: dict) -> str | None:
    for key in ("consent_url", "url", "authorization_url", "auth_url"):
        v = payload.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v

    def walk(obj: Any) -> str | None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and v.startswith("http") and (
                    "consent" in k.lower() or "auth" in k.lower() or "login.microsoftonline" in v
                ):
                    return v
                r = walk(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = walk(item)
                if r:
                    return r
        return None

    return walk(payload)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prompt", help="natural-language message to send to the agent")
    p.add_argument(
        "--session-id",
        default=os.environ.get("AGENT_PROJECT_ID") or None,
        help=(
            "Bind this turn to an existing Foundry sandbox (agent_session_id). "
            "Falls back to AGENT_PROJECT_ID env var. If unset, the platform "
            "creates a fresh sandbox and prints the id back; reuse it on the "
            "next call to land in the same per-session $HOME."
        ),
    )
    p.add_argument(
        "--previous-response-id",
        default=None,
        help="Thread this call into an existing response chain (rarely needed for one-shot kickoff).",
    )
    args = p.parse_args()

    if not AGENT_RESPONSES_URL:
        fail("set AGENT_RESPONSES_URL=https://<deployed-agent>/responses")

    token = acquire_user_token()

    response_id, session_id, completed, consent = call_agent(
        token,
        args.prompt,
        previous_response_id=args.previous_response_id,
        agent_session_id=args.session_id,
    )

    if consent:
        url = extract_consent_url(consent)
        if url:
            print(f"\n[consent] open this URL in a browser, grant consent, then press Enter:\n  {url}")
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001
                pass
        else:
            print(f"\n[consent] consent payload received but no URL extracted:\n{json.dumps(consent, indent=2)}")
        input("\n[consent] press Enter once you have consented... ")
        time.sleep(1)
        response_id, session_id, completed, consent = call_agent(
            token,
            args.prompt,
            previous_response_id=response_id,
            agent_session_id=session_id,
        )

    print("\n" + "=" * 60)
    if completed:
        print("FINAL ANSWER:\n")
        print(completed)
    else:
        print("No response.completed text captured. Inspect the event log above.")
    print("=" * 60)
    if session_id:
        print(f"\nAGENT_SESSION_ID: {session_id}")
        print("Reuse it on the next turn with:")
        print(f"  python spike/desktop_to_foundry/chat.py --session-id {session_id} \"<your next prompt>\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
