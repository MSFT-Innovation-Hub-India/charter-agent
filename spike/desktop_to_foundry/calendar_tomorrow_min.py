"""Minimal variant of ``calendar_today.py``: POSTs one calendar question to
the deployed charter-agent ``/responses`` endpoint, streams SSE, prints the
tool calls and the final text. No consent-flow handling, no resume; uses
``DefaultAzureCredential`` (picks up ``az login``). Useful for a quick smoke
of an already-deployed agent. For the full per-user passthrough + consent
spike see ``calendar_today.py`` in this folder.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
from azure.identity import DefaultAzureCredential

ENDPOINT = "https://ocvp-agent-svc-resource.services.ai.azure.com/api/projects/ocvp-agent-svc/agents/charter-agent/endpoint/protocols/openai/responses?api-version=v1"
# Session binding (Foundry hosted-sessions docs):
#  - If AGENT_PROJECT_ID is set, send it as agent_session_id on the call.
#  - Otherwise the platform creates a sandbox and echoes the id back; we just
#    print it so the caller knows what to reuse on a follow-up.
PROJECT_ID = (os.environ.get("AGENT_PROJECT_ID") or "").strip() or None

async def main():
    cred = DefaultAzureCredential()
    token = cred.get_token("https://ai.azure.com/.default").token
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if PROJECT_ID:
        headers["x-agent-chat-isolation-key"] = PROJECT_ID
    body = {
        "input": "Look up my real Outlook calendar via the WorkIQ tools and tell me how tomorrow (Saturday May 23, 2026) looks. Today is Friday May 22, 2026. Give one short paragraph plus a bullet list of events with start-end time, subject, and key attendees. Do not invent any events.",
        "model": "gpt-5.4",
        "stream": True,
        "store": False,
    }
    if PROJECT_ID:
        body["agent_session_id"] = PROJECT_ID
        print(f"agent_session_id (client-supplied)={PROJECT_ID!r}", flush=True)
    else:
        print("agent_session_id: not supplied; platform will create one", flush=True)
    returned_session_id = None
    final = []
    tool_calls = []
    terminal = None
    async with httpx.AsyncClient(timeout=300) as c:
        async with c.stream("POST", ENDPOINT, json=body, headers=headers) as r:
            print("HTTP", r.status_code, flush=True)
            current = None
            async for line in r.aiter_lines():
                if not line: continue
                if line.startswith("event:"): current = line[6:].strip(); continue
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    try: obj = json.loads(payload)
                    except: continue
                    if current == "response.output_text.delta": final.append(obj.get("delta",""))
                    elif current == "response.created":
                        # Platform echoes agent_session_id on the response object.
                        resp = obj.get("response", {})
                        returned_session_id = (
                            resp.get("agent_session_id")
                            or obj.get("agent_session_id")
                            or returned_session_id
                        )
                    elif current == "response.output_item.added":
                        item = obj.get("item",{})
                        if item.get("type") in ("function_call","tool_call"):
                            tool_calls.append({"name": item.get("name"), "args":""})
                    elif current == "response.function_call_arguments.delta":
                        if tool_calls: tool_calls[-1]["args"] += obj.get("delta","")
                    elif current == "response.function_call_arguments.done":
                        if tool_calls: tool_calls[-1]["args_final"] = obj.get("arguments")
                    elif current in ("response.completed","response.failed"):
                        terminal = current
                        resp = obj.get("response",{})
                        returned_session_id = (
                            resp.get("agent_session_id")
                            or obj.get("agent_session_id")
                            or returned_session_id
                        )
                        print("TERMINAL:", current, "status:", resp.get("status"), flush=True)
                        if resp.get("error"):
                            print("ERROR:", json.dumps(resp["error"]), flush=True)
    print("TOOL_CALLS_COUNT:", len(tool_calls), flush=True)
    for tc in tool_calls:
        print("CALL:", tc.get("name"), "ARGS:", tc.get("args_final") or tc.get("args"), flush=True)
    if returned_session_id:
        print("AGENT_SESSION_ID:", returned_session_id, flush=True)
    print("=====FINAL=====", flush=True)
    print("".join(final) or "(empty)", flush=True)

asyncio.run(main())
