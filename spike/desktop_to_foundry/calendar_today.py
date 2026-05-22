"""Throwaway desktop spike: prove per-user OAuth Identity Passthrough works
end-to-end from a desktop client → deployed Foundry hosted agent → Toolbox →
WorkIQ Calendar MCP.

Run flow:
  1. Acquire a user token via `az login` (AzureCliCredential).
  2. POST one message to `<AGENT_RESPONSES_URL>` with `Authorization: Bearer <user_token>`.
  3. Stream SSE events. If an `oauth_consent_request` arrives, print the URL,
     wait for the user to consent in a browser, then resume with
     `previous_response_id`.
  4. Print the final text from `response.completed`.

Prereq: `az login -t <tenant>` as the user whose calendar you want to query.
See README.md in this folder for more.
"""

from __future__ import annotations

import json
import os
import sys
import time
import webbrowser
from typing import Any

import httpx
from azure.identity import AzureCliCredential

PROMPT = "What does my day look like in the calendar today?"

AGENT_RESPONSES_URL = os.environ.get("AGENT_RESPONSES_URL", "").strip()
TENANT_ID = os.environ.get("SPIKE_TENANT_ID") or None
SCOPE = "https://ai.azure.com/.default"
HTTP_TIMEOUT = httpx.Timeout(300.0, connect=30.0)


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
    """Yield parsed SSE event dicts from a streaming Responses-protocol response.

    The Responses protocol uses one or both of:
      event: <name>
      data: <json>
    blocks separated by blank lines. We tolerate either shape.
    """
    event_name: str | None = None
    data_lines: list[str] = []
    for raw in response.iter_lines():
        line = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                parsed: Any
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    parsed = {"raw": payload}
                yield {"event": event_name or parsed.get("type") or "message", "data": parsed}
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
) -> tuple[str | None, str | None, dict | None]:
    """POST one /responses turn, stream SSE, print events.

    Returns: (response_id, completed_text, consent_request_payload).
    """
    body: dict[str, Any] = {"input": prompt, "stream": True, "store": False}
    if previous_response_id:
        body["previous_response_id"] = previous_response_id

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    response_id: str | None = None
    completed_text: str | None = None
    consent_payload: dict | None = None

    print(f"\n[call] POST {AGENT_RESPONSES_URL}")
    if previous_response_id:
        print(f"[call] resuming previous_response_id={previous_response_id}")
    print(f"[call] prompt: {prompt!r}\n")

    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        with client.stream("POST", AGENT_RESPONSES_URL, json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                err = resp.read().decode("utf-8", errors="replace")
                fail(f"HTTP {resp.status_code}: {err[:2000]}")
            for evt in iter_sse_events(resp):
                name = evt["event"]
                data = evt["data"]
                etype = data.get("type") if isinstance(data, dict) else None
                tag = etype or name
                print(f"  ← {tag}")

                if not isinstance(data, dict):
                    continue

                if data.get("type") == "response.created":
                    response_id = data.get("response", {}).get("id") or data.get("id")

                # Surface error payloads so we can diagnose server-side failures.
                if tag in ("response.failed", "error", "response.error") or (
                    isinstance(etype, str) and (etype.endswith(".failed") or etype.endswith(".error"))
                ):
                    print(f"     -> payload: {json.dumps(data, indent=2)[:3000]}")

                # The exact event name for consent prompts is still evolving in
                # the platform. Be liberal: catch anything that looks like it.
                if (
                    "oauth_consent" in tag.lower()
                    or "consent" in (data.get("type") or "").lower()
                    or "consent_url" in json.dumps(data).lower()
                ):
                    consent_payload = data
                    print(f"     -> consent payload: {json.dumps(data, indent=2)[:1500]}")

                if data.get("type") == "response.output_text.delta":
                    delta = data.get("delta") or ""
                    if delta:
                        print(delta, end="", flush=True)

                if data.get("type") == "response.completed":
                    print(f"     -> payload: {json.dumps(data, indent=2)[:4000]}")
                    out = data.get("response", {}).get("output") or []
                    text_parts: list[str] = []
                    for item in out:
                        for c in (item.get("content") or []):
                            if c.get("type") in ("output_text", "text") and c.get("text"):
                                text_parts.append(c["text"])
                    if text_parts:
                        completed_text = "\n".join(text_parts)

    print()  # newline after any inline streaming text
    return response_id, completed_text, consent_payload


def extract_consent_url(payload: dict) -> str | None:
    """Best-effort: dig out a consent URL from a consent-request event payload."""
    blob = json.dumps(payload)
    for key in ("consent_url", "url", "authorization_url", "auth_url"):
        if key in payload and isinstance(payload[key], str) and payload[key].startswith("http"):
            return payload[key]
    # nested search
    def walk(obj: Any) -> str | None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and v.startswith("http") and ("consent" in k.lower() or "auth" in k.lower() or "login.microsoftonline" in v):
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
    return walk(payload) or (blob if "consent" in blob.lower() else None)


def main() -> int:
    if not AGENT_RESPONSES_URL:
        fail("set AGENT_RESPONSES_URL=https://<deployed-agent>/responses")

    token = acquire_user_token()

    response_id, completed, consent = call_agent(token, PROMPT, previous_response_id=None)

    if consent:
        url = extract_consent_url(consent)
        if url and url.startswith("http"):
            print(f"\n[consent] open this URL in a browser, grant consent, then press Enter:\n  {url}")
            try:
                webbrowser.open(url)
            except Exception:
                pass
        else:
            print(f"\n[consent] consent payload received but no URL extracted:\n{json.dumps(consent, indent=2)}")
        input("\n[consent] press Enter once you have consented... ")
        time.sleep(1)
        response_id, completed, consent = call_agent(
            token, PROMPT, previous_response_id=response_id
        )

    print("\n" + "=" * 60)
    if completed:
        print("FINAL ANSWER:\n")
        print(completed)
    else:
        print("No response.completed text captured. Inspect the event log above.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
