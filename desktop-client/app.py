"""Project Charter Desktop Agent.

A pywebview rich client for the charter-agent. Supports both endpoints:

  - **local**   — a charter-agent running on this machine
                  (e.g. `python -m charter_agent` against
                  http://localhost:8088/responses).
  - **hosted**  — the deployed Foundry-hosted agent.

The endpoint is picked at startup (env var or CLI flag) and switchable at
runtime via the dropdown in the window header; the current session id is
dropped on switch so the next message lands in the right sandbox.

Run::

    cd desktop-client
    python -m venv .venv
    .\\.venv\\Scripts\\Activate.ps1
    pip install -r requirements.txt

    # one of (or both):
    $env:AGENT_ENDPOINT_LOCAL  = "http://localhost:8088/responses"
    $env:AGENT_ENDPOINT_HOSTED = "https://<deployed-agent>/responses"

    # default mode (overridable in-window)
    $env:AGENT_ENDPOINT_MODE = "hosted"   # or "local"

    az login
    python app.py

First run shows the native Windows account picker (WAM broker via
`azure-identity-broker`), modal to the app window; subsequent runs reuse the
saved `AuthenticationRecord` from `~/.charter-agent/auth_records/` and silently
refresh tokens from the persistent MSAL cache. If the broker is unavailable
(no `pymsalruntime`, non-Windows, etc.) we fall back to
`InteractiveBrowserCredential` which opens the system browser.

Identity is propagated to WorkIQ via Foundry OAuth Identity Passthrough — the
desktop client signs you in, attaches the bearer to /responses, and the
platform exchanges it per Toolbox connection. No refresh tokens leave the
local process.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import pathlib
import re
import sys
import threading
import time
import webbrowser
from typing import Any

# Silence WebView2's accessibility-tree walker. On Windows the provider hits a
# recursive AccessibilityObject.Bounds.Empty loop on shutdown that dumps a
# multi-KB stderr stack — harmless, but noisy. Disabling the accessibility
# provider is fine for a single-user dev tool.
os.environ.setdefault("PYWEBVIEW_DISABLE_ACCESSIBILITY", "1")

import httpx
import webview
from azure.identity import (
    AuthenticationRecord,
    InteractiveBrowserCredential,
    TokenCachePersistenceOptions,
)

try:  # azure-identity-broker is optional but strongly preferred on Windows.
    from azure.identity.broker import InteractiveBrowserBrokerCredential  # type: ignore
    _BROKER_AVAILABLE = True
except Exception:  # noqa: BLE001
    InteractiveBrowserBrokerCredential = None  # type: ignore[assignment]
    _BROKER_AVAILABLE = False

DEFAULT_LOCAL = "http://localhost:8088/responses"
TENANT_ID = os.environ.get("SPIKE_TENANT_ID") or None
SCOPE = "https://ai.azure.com/.default"
HTTP_TIMEOUT = httpx.Timeout(600.0, connect=30.0)

UI_HTML = pathlib.Path(__file__).with_name("ui.html")

AUTH_CACHE_NAME = "charter-agent-desktop"
_APP_HOME = pathlib.Path.home() / ".charter-agent"
_AUTH_RECORD_PATH = _APP_HOME / "auth_records" / f"{AUTH_CACHE_NAME}.json"

logger = logging.getLogger("charter_desktop")


def _iter_sse_events(response: httpx.Response):
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


def _extract_consent_url(payload: dict) -> str | None:
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


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.MULTILINE)


def _maybe_extract_dashboard(text: str) -> dict | None:
    for m in _JSON_BLOCK_RE.finditer(text or ""):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("kind") == "dashboard":
            return obj
    return None


def _decode_jwt_name(token: str) -> str | None:
    """Pull a display name out of an Azure AD access token without verifying it.

    We're not validating signature — just extracting claims for UI display. The
    token is still verified end-to-end by the Foundry platform; the client only
    uses it to greet the user. Returns the first of `name`,
    `preferred_username`, `upn`, or `unique_name` that's present.
    """
    try:
        import base64
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    for key in ("name", "preferred_username", "upn", "unique_name"):
        val = claims.get(key)
        if isinstance(val, str) and val:
            return val
    return None


class Bridge:
    """JS-callable surface exposed to the WebView via pywebview's js_api."""

    def __init__(self, *, local_url: str, hosted_url: str, initial_mode: str) -> None:
        self.window: webview.Window | None = None
        self.token: str | None = None
        self.token_expires_at: float = 0.0
        self.user_name: str | None = None
        self._credential: Any = None
        self._record_saved: bool = _AUTH_RECORD_PATH.exists()
        self.session_id: str | None = os.environ.get("AGENT_PROJECT_ID") or None
        self.previous_response_id: str | None = None
        self.endpoints = {"local": local_url, "hosted": hosted_url}
        self.mode: str = initial_mode if initial_mode in self.endpoints else "hosted"
        self._lock = threading.Lock()

    # ---------- lifecycle ----------

    def ready(self) -> dict:
        return {
            "endpoints": self.endpoints,
            "mode": self.mode,
            "agent_url": self.endpoints.get(self.mode, ""),
            "session_id": self.session_id,
            "tenant_id": TENANT_ID,
            "scope": SCOPE,
            "user_name": self.user_name,
        }

    def set_mode(self, mode: str) -> dict:
        if mode not in self.endpoints:
            return {"ok": False, "error": f"unknown mode: {mode}"}
        if not self.endpoints[mode]:
            return {"ok": False, "error": f"endpoint for mode={mode!r} is not configured"}
        self.mode = mode
        self.session_id = None
        self.previous_response_id = None
        return {"ok": True, "mode": mode, "agent_url": self.endpoints[mode]}

    def login(self) -> dict:
        try:
            if self._credential is None:
                self._credential = _build_credential()
            tok = self._credential.get_token(SCOPE)
            # On the first successful sign-in, capture an AuthenticationRecord so
            # subsequent launches reuse the account silently without re-prompting.
            if not self._record_saved:
                try:
                    rec = self._credential.authenticate(scopes=[SCOPE])
                    _save_record(rec)
                    self._record_saved = True
                except Exception as ex:  # noqa: BLE001
                    logger.debug("auth: could not capture record: %s", ex)
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "error": (
                    f"Interactive sign-in failed: {e}. The Windows account picker "
                    "(or browser) should appear on next attempt; if it doesn't, "
                    "`pip install azure-identity-broker` and retry."
                ),
            }
        self.token = tok.token
        self.token_expires_at = float(tok.expires_on)
        self.user_name = _decode_jwt_name(tok.token)
        return {"ok": True, "expires_on": int(tok.expires_on), "user_name": self.user_name}

    def reset_session(self) -> dict:
        self.session_id = None
        self.previous_response_id = None
        return {"ok": True}

    # ---------- chat ----------

    def send(self, prompt: str) -> dict:
        url = self.endpoints.get(self.mode)
        if not url:
            return {"ok": False, "error": f"endpoint for mode={self.mode!r} is not configured."}
        if not self.token or time.time() >= self.token_expires_at - 60:
            r = self.login()
            if not r.get("ok"):
                return r
        threading.Thread(target=self._run_turn, args=(prompt, url), daemon=True).start()
        return {"ok": True}

    # ---------- internals ----------

    def _emit(self, event: str, payload: dict | None = None) -> None:
        if not self.window:
            return
        msg = json.dumps({"event": event, "payload": payload or {}})
        try:
            self.window.evaluate_js(f"window.onAgentEvent && window.onAgentEvent({msg})")
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] evaluate_js failed: {e}", file=sys.stderr)

    def _run_turn(self, prompt: str, url: str) -> None:
        with self._lock:
            self._emit("turn.start", {"prompt": prompt, "session_id": self.session_id, "mode": self.mode})
            try:
                self._post_one(prompt, url=url, previous_response_id=self.previous_response_id)
            except Exception as e:  # noqa: BLE001
                self._emit("turn.error", {"error": str(e)})

    def _post_one(self, prompt: str, *, url: str, previous_response_id: str | None) -> None:
        body: dict[str, Any] = {"input": prompt, "stream": True, "store": False}
        if self.session_id:
            body["agent_session_id"] = self.session_id
        if previous_response_id:
            body["previous_response_id"] = previous_response_id
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.session_id:
            headers["x-agent-chat-isolation-key"] = self.session_id

        response_id: str | None = None
        completed_text_parts: list[str] = []
        consent_payload: dict | None = None
        current_tool: dict[str, str] | None = None

        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code >= 400:
                    err = resp.read().decode("utf-8", errors="replace")
                    self._emit("turn.error", {"error": f"HTTP {resp.status_code}: {err[:2000]}"})
                    return
                for evt in _iter_sse_events(resp):
                    data = evt["data"]
                    if not isinstance(data, dict):
                        continue
                    etype = data.get("type") or evt["event"]

                    if etype == "response.created":
                        resp_obj = data.get("response") or {}
                        response_id = resp_obj.get("id") or data.get("id")
                        sid = resp_obj.get("agent_session_id") or data.get("agent_session_id")
                        if sid and sid != self.session_id:
                            self.session_id = sid
                            self._emit("session.update", {"session_id": sid})
                        continue

                    if etype == "response.output_item.added":
                        item = data.get("item") or {}
                        if item.get("type") in ("function_call", "tool_call"):
                            current_tool = {"name": str(item.get("name") or "?"), "args": ""}
                            self._emit("tool.call", {"name": current_tool["name"]})
                        continue

                    if etype == "response.function_call_arguments.delta":
                        if current_tool is not None:
                            current_tool["args"] += str(data.get("delta") or "")
                        continue

                    if etype == "response.function_call_arguments.done":
                        if current_tool is not None:
                            current_tool["args"] = str(data.get("arguments") or current_tool["args"])
                            self._emit("tool.args", {"name": current_tool["name"], "args": current_tool["args"][:1200]})
                        continue

                    if etype == "response.output_text.delta":
                        delta = data.get("delta") or ""
                        if delta:
                            self._emit("text.delta", {"delta": delta})
                        continue

                    if etype == "response.completed":
                        resp_obj = data.get("response") or {}
                        sid = resp_obj.get("agent_session_id") or data.get("agent_session_id")
                        if sid and sid != self.session_id:
                            self.session_id = sid
                            self._emit("session.update", {"session_id": sid})
                        out = resp_obj.get("output") or []
                        for it in out:
                            for c in (it.get("content") or []):
                                if c.get("type") in ("output_text", "text") and c.get("text"):
                                    completed_text_parts.append(c["text"])
                        continue

                    if etype and ("oauth_consent" in etype.lower() or "consent" in etype.lower()):
                        consent_payload = data
                        continue

                    if etype and (etype.endswith(".failed") or etype.endswith(".error")):
                        self._emit("turn.error", {"error": f"{etype}: {json.dumps(data)[:1500]}"})
                        return

        final_text = "\n".join(completed_text_parts).strip()
        if response_id:
            self.previous_response_id = response_id

        if consent_payload:
            url2 = _extract_consent_url(consent_payload)
            if url2:
                self._emit("consent.required", {"url": url2})
                try:
                    webbrowser.open(url2)
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(8)
                self._post_one(prompt, url=url, previous_response_id=response_id)
                return

        dashboard = _maybe_extract_dashboard(final_text)
        self._emit("turn.complete", {
            "response_id": response_id,
            "session_id": self.session_id,
            "text": final_text,
            "dashboard": dashboard,
        })


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["local", "hosted"], default=os.environ.get("AGENT_ENDPOINT_MODE", "hosted"))
    p.add_argument("--local-url", default=os.environ.get("AGENT_ENDPOINT_LOCAL", DEFAULT_LOCAL))
    p.add_argument(
        "--hosted-url",
        default=os.environ.get("AGENT_ENDPOINT_HOSTED") or os.environ.get("AGENT_RESPONSES_URL", ""),
        help="URL of the deployed Foundry-hosted agent's /responses endpoint.",
    )
    args = p.parse_args()

    if args.mode == "hosted" and not args.hosted_url:
        print("[warn] AGENT_ENDPOINT_HOSTED not set; starting in 'local' mode.", file=sys.stderr)
        args.mode = "local"

    if not UI_HTML.exists():
        print(f"!! missing ui.html at {UI_HTML}", file=sys.stderr)
        return 1

    bridge = Bridge(local_url=args.local_url, hosted_url=args.hosted_url, initial_mode=args.mode)
    window = webview.create_window(
        "Project Charter Desktop Agent",
        url=str(UI_HTML),
        js_api=bridge,
        width=1280,
        height=860,
        min_size=(960, 640),
    )
    bridge.window = window

    def _on_started() -> None:
        # Register the pywebview HWND so the WAM account picker is parented to
        # our window instead of floating loose. FindWindowW by title is the
        # most portable way across pywebview versions.
        if sys.platform == "win32":
            try:
                hwnd = ctypes.windll.user32.FindWindowW(None, "Project Charter Desktop Agent")
                _set_parent_hwnd(int(hwnd))
            except Exception as ex:  # noqa: BLE001
                logger.debug("auth: HWND lookup failed: %s", ex)

    webview.start(_on_started, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
