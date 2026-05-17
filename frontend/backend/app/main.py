"""Charter frontend BFF — Phase 1 skeleton.

Routes:
  GET  /p/{project_id}         → serve SPA shell
  POST /p/{project_id}/invoke  → forward to agent /invocations with project_id as
                                 chat-isolation key + FOUNDRY_AGENT_SESSION_ID

Auth (MSAL) and OBO are wired in later phases.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="charter-agent BFF")

AGENT_INVOCATIONS_URL = os.environ.get("AGENT_INVOCATIONS_URL", "http://localhost:8088/invocations")

_SPA_HTML = """<!doctype html>
<html><head><meta charset=\"utf-8\"><title>Charter Agent</title></head>
<body>
  <h1>Charter Agent — project <span id=\"pid\"></span></h1>
  <button id=\"go\">echo</button>
  <pre id=\"out\"></pre>
  <script>
    const pid = location.pathname.split('/')[2];
    document.getElementById('pid').textContent = pid;
    document.getElementById('go').onclick = async () => {
      const r = await fetch(`/p/${pid}/invoke`, {
        method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({action: 'echo', payload: {message: 'hello'}})
      });
      document.getElementById('out').textContent = await r.text();
    };
  </script>
</body></html>
"""


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/p/{project_id}", response_class=HTMLResponse)
def spa(project_id: str) -> str:
    return _SPA_HTML


@app.post("/p/{project_id}/invoke")
async def invoke(project_id: str, request: Request) -> JSONResponse:
    body: dict[str, Any] = await request.json()
    headers = {
        "x-ms-chat-isolation-key": project_id,
        "x-foundry-agent-session-id": project_id,
        "content-type": "application/json",
    }
    auth = request.headers.get("authorization")
    if auth:
        headers["authorization"] = auth
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(AGENT_INVOCATIONS_URL, json=body, headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"agent unreachable: {e}") from e
    return JSONResponse(status_code=r.status_code, content={"raw": r.text})
