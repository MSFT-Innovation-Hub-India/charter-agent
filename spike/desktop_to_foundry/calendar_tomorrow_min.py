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

import httpx
from azure.identity import DefaultAzureCredential

ENDPOINT = "https://ocvp-agent-svc-resource.services.ai.azure.com/api/projects/ocvp-agent-svc/agents/charter-agent/endpoint/protocols/openai/responses?api-version=v1"

async def main():
    cred = DefaultAzureCredential()
    token = cred.get_token("https://ai.azure.com/.default").token
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "input": "Look up my real Outlook calendar via the WorkIQ tools and tell me how tomorrow (Saturday May 23, 2026) looks. Today is Friday May 22, 2026. Give one short paragraph plus a bullet list of events with start-end time, subject, and key attendees. Do not invent any events.",
        "model": "gpt-5.4",
        "stream": True,
        "store": False,
    }
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
                        print("TERMINAL:", current, "status:", resp.get("status"), flush=True)
                        if resp.get("error"):
                            print("ERROR:", json.dumps(resp["error"]), flush=True)
    print("TOOL_CALLS_COUNT:", len(tool_calls), flush=True)
    for tc in tool_calls:
        print("CALL:", tc.get("name"), "ARGS:", tc.get("args_final") or tc.get("args"), flush=True)
    print("=====FINAL=====", flush=True)
    print("".join(final) or "(empty)", flush=True)

asyncio.run(main())
