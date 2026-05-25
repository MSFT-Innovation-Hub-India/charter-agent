"""Generic one-shot desktop client for the deployed charter-agent.

A sibling of `calendar_today.py` whose only difference is that the prompt is a
CLI argument instead of a hardcoded calendar question. Use it to drive any
workflow the agent supports — SOW kickoff, resume / status, ad-hoc M365 query.

## Session model (two-key, per AGENTS.md §9.1)

Two orthogonal IDs travel on every /responses call:

  agent_session_id   — bound to the Foundry microVM and its $HOME (persists
                       across container restarts, up to 30 days). Identifies
                       the project sandbox. Pass it on every turn to land in
                       the same project.

  previous_response_id — bound to the in-memory transcript store (lost on
                         container restart). Gives the model multi-turn history.
                         Can be omitted or cleared without losing project state.

Silent empty-completion failure: if previous_response_id references a transcript
that has rolled (container restarted), the endpoint returns HTTP 200 with zero
output events. Recovery: retry the same prompt with previous_response_id=None
and the SAME agent_session_id. Never clear the session_id — that orphans the
project sandbox.

## Project preamble

Every message is prefixed with a context line the host runtime parses to
set the active project sandbox before the model runs:

    [charter-agent-context: project_id=<id> is_new=<true|false> skill=<name>]

The host strips this line before the skill sees the message. Including
`skill=<name>` on every turn lets the host dispatch to the correct warm Agent
without reading the project log — this matters on the first turn of a new
project before any log exists.

## Usage

    $env:AGENT_RESPONSES_URL = "https://<your-deployed-agent>/responses"
    az login

    # First turn — new project (omit --session-id; platform creates one)
    python spike/desktop_to_foundry/chat.py \\
        --project-id p-northwind-01 --skill sow-response \\
        "I just had a Teams meeting about the Northwind RFP. Pull the notes and kick off the SOW."

    # Subsequent turns — same project (reuse session-id + project-id)
    python spike/desktop_to_foundry/chat.py \\
        --session-id <captured-id> --project-id p-northwind-01 --skill sow-response \\
        --previous-response-id <captured-response-id> \\
        "What's the status of the Northwind SOW?"
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


def build_preamble(project_id: str | None, is_new: bool, skill: str | None) -> str:
    """Return the context preamble line the host runtime parses for routing."""
    if not project_id:
        return ""
    parts = [f"project_id={project_id}", f"is_new={'true' if is_new else 'false'}"]
    if skill:
        parts.append(f"skill={skill}")
    return f"[charter-agent-context: {' '.join(parts)}]\n"


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
    project_id: str | None = None,
    is_new: bool = False,
    skill: str | None = None,
) -> tuple[str | None, str | None, str | None, dict | None, int]:
    """POST one /responses turn, stream SSE, print events.

    Returns: (response_id, agent_session_id, completed_text, consent_payload, output_event_count).
    The output_event_count lets the caller detect the silent empty-completion
    failure mode (container restarted; previous_response_id no longer valid).
    """
    preamble = build_preamble(project_id, is_new, skill)
    full_input = preamble + prompt

    body: dict[str, Any] = {"input": full_input, "stream": True, "store": False}
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
    output_event_count = 0

    print(f"\n[bridge] POST {AGENT_RESPONSES_URL}")
    if agent_session_id:
        print(f"[bridge] session={agent_session_id!r}  prev_resp={previous_response_id!r}")
    else:
        print("[bridge] no agent_session_id sent; platform will create one")
    if project_id:
        print(f"[bridge] project={project_id!r}  skill={skill!r}  is_new={is_new}")
    print(f"[bridge] prompt: {prompt!r}\n")

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
                    print(f"[bridge] evt#1 type=response.created response_id={response_id}")
                    output_event_count += 1
                    continue

                if tag == "response.output_item.added":
                    item = data.get("item") or {}
                    if item.get("type") in ("function_call", "tool_call"):
                        tool_calls.append({"name": str(item.get("name") or "?"), "args": ""})
                        print(f"  → tool call: {item.get('name')}")
                    output_event_count += 1
                    continue

                if tag == "response.function_call_arguments.delta":
                    if tool_calls:
                        tool_calls[-1]["args"] += str(data.get("delta") or "")
                    continue

                if tag == "response.function_call_arguments.done":
                    if tool_calls:
                        tool_calls[-1]["args_final"] = str(data.get("arguments") or "")
                        preview = (tool_calls[-1].get("args_final") or "")[:400]
                        print(f"     args: {preview}")
                    output_event_count += 1
                    continue

                if tag == "response.output_text.delta":
                    delta = data.get("delta") or ""
                    if delta:
                        print(delta, end="", flush=True)
                    output_event_count += 1
                    continue

                if tag == "response.completed":
                    print()
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
                    print(f"[bridge] stream done: events={output_event_count} response_id={response_id} text_parts={len(text_parts)} consent={'yes' if consent_payload else 'no'}")
                    print(f"  ← response.completed (status={resp_obj.get('status')})")
                    output_event_count += 1
                    continue

                if tag in ("response.failed", "error", "response.error") or (
                    isinstance(etype, str) and (etype.endswith(".failed") or etype.endswith(".error"))
                ):
                    print(f"  ← {tag}\n     payload: {json.dumps(data, indent=2)[:3000]}")
                    output_event_count += 1
                    continue

                if (
                    "oauth_consent" in tag.lower()
                    or "consent" in (data.get("type") or "").lower()
                    or "consent_url" in json.dumps(data).lower()
                ):
                    consent_payload = data
                    print(f"  ← {tag}\n     consent payload: {json.dumps(data, indent=2)[:1500]}")
                    output_event_count += 1
                    continue

    print()
    if returned_session_id:
        print(f"[bridge] <- agent_session_id={returned_session_id!r}")
    return response_id, returned_session_id, completed_text, consent_payload, output_event_count


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
        default=os.environ.get("AGENT_SESSION_ID") or None,
        help=(
            "Bind this turn to an existing Foundry sandbox (agent_session_id). "
            "Falls back to AGENT_SESSION_ID env var. If unset, the platform "
            "creates a fresh sandbox and prints the id back."
        ),
    )
    p.add_argument(
        "--project-id",
        default=os.environ.get("AGENT_PROJECT_ID") or None,
        help=(
            "Project id sent in the [charter-agent-context] preamble so the "
            "host sets the right per-project $HOME sandbox before the model "
            "runs. Falls back to AGENT_PROJECT_ID env var."
        ),
    )
    p.add_argument(
        "--skill",
        default=os.environ.get("AGENT_SKILL") or "sow-response",
        help="Skill name to include in the preamble (default: sow-response).",
    )
    p.add_argument(
        "--is-new",
        action="store_true",
        default=False,
        help="Mark the project as new in the preamble (first turn of a brand-new project).",
    )
    p.add_argument(
        "--previous-response-id",
        default=None,
        help="Thread this call into an existing response chain for multi-turn continuity.",
    )
    args = p.parse_args()

    if not AGENT_RESPONSES_URL:
        fail("set AGENT_RESPONSES_URL=https://<deployed-agent>/responses")

    token = acquire_user_token()

    call_kwargs: dict[str, Any] = dict(
        previous_response_id=args.previous_response_id,
        agent_session_id=args.session_id,
        project_id=args.project_id,
        is_new=args.is_new,
        skill=args.skill,
    )

    response_id, session_id, completed, consent, event_count = call_agent(
        token, args.prompt, **call_kwargs
    )

    # ── Consent flow ────────────────────────────────────────────────────────
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
        response_id, session_id, completed, consent, event_count = call_agent(
            token, args.prompt,
            previous_response_id=response_id,
            agent_session_id=session_id,
            project_id=args.project_id,
            is_new=args.is_new,
            skill=args.skill,
        )

    # ── Silent empty-completion recovery (AGENTS.md §9.1) ───────────────────
    # If previous_response_id was sent but the transcript has rolled (container
    # restarted), the endpoint returns HTTP 200 with zero meaningful output.
    # Retry with previous_response_id=None and the SAME agent_session_id —
    # never clear session_id or we orphan the project sandbox.
    if (
        args.previous_response_id
        and not consent
        and not completed
        and event_count <= 4
    ):
        print(
            "\n[bridge] WARNING: previous_response_id sent but no output received — "
            "transcript may have rolled (container restart). Retrying with "
            "previous_response_id=None and same session_id..."
        )
        response_id, session_id, completed, consent, event_count = call_agent(
            token, args.prompt,
            previous_response_id=None,  # cleared — transcript is gone
            agent_session_id=session_id or args.session_id,  # microVM still intact
            project_id=args.project_id,
            is_new=args.is_new,
            skill=args.skill,
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
        pid_flag = f" --project-id {args.project_id}" if args.project_id else ""
        skill_flag = f" --skill {args.skill}" if args.skill else ""
        print(
            f"  python spike/desktop_to_foundry/chat.py"
            f" --session-id {session_id}"
            f"{pid_flag}"
            f"{skill_flag}"
            f" --previous-response-id {response_id}"
            f' "<your next prompt>"'
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
