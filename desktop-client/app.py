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

import httpx
import webview

# Silence pywebview's WinForms backend logger. On WebView2 shutdown it tries to
# repr `window.native` for diagnostics and trips a recursive
# AccessibilityObject.Bounds.Empty loop, dumping multi-KB stderr stacks.
# Harmless but spammy; mute it for the desktop tool.
for _name in ("pywebview", "webview", "pywebview.winforms"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)


class _SuppressPywebviewRecursion(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "AccessibilityObject" not in msg and "maximum recursion depth" not in msg


logging.getLogger().addFilter(_SuppressPywebviewRecursion())
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

# In local dev the agent's sandbox $HOME is a sibling directory:
#   <repo>/agent/.charter-agent-home/
#       projects/<pid>/project_log.json   (per-project state, source of truth)
#       activity.json                     (global agent audit log; NDJSON)
# The client reads these directly to restore the dashboard on project switch /
# app launch without spending a model turn. In hosted mode `AGENT_HOME` can be
# overridden via env to a mounted location.
_AGENT_HOME = pathlib.Path(
    os.environ.get("AGENT_HOME")
    or (pathlib.Path(__file__).resolve().parent.parent / "agent" / ".charter-agent-home")
)

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


def _load_record() -> AuthenticationRecord | None:
    """Read the saved `AuthenticationRecord` from disk, if any."""
    if not _AUTH_RECORD_PATH.exists():
        return None
    try:
        return AuthenticationRecord.deserialize(_AUTH_RECORD_PATH.read_text(encoding="utf-8"))
    except Exception as ex:  # noqa: BLE001
        logger.warning("auth: failed to load record (%s); will re-prompt.", ex)
        return None


def _save_record(record: AuthenticationRecord) -> None:
    """Persist the `AuthenticationRecord` so future launches are silent."""
    _AUTH_RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    _AUTH_RECORD_PATH.write_text(record.serialize(), encoding="utf-8")


def _build_credential(parent_hwnd: int = 0) -> Any:
    """Build the interactive credential used for sign-in.

    Prefers `InteractiveBrowserBrokerCredential` (Windows account picker via
    WAM) when `azure-identity-broker` is installed; otherwise falls back to
    `InteractiveBrowserCredential` (system browser). Both reuse a persisted
    `AuthenticationRecord` to make repeat launches silent.

    `parent_hwnd` should be the HWND of the foreground app window. WAM
    cancels the request immediately when the handle is 0/invalid, so we
    fall back to `GetForegroundWindow()` and then to the browser credential
    when no usable handle is available.
    """
    record = _load_record()
    cache_opts = TokenCachePersistenceOptions(name=AUTH_CACHE_NAME)
    common: dict[str, Any] = {
        "cache_persistence_options": cache_opts,
        "authentication_record": record,
    }
    if TENANT_ID:
        common["tenant_id"] = TENANT_ID

    if _BROKER_AVAILABLE and InteractiveBrowserBrokerCredential is not None:
        hwnd = parent_hwnd
        if not hwnd:
            try:
                import ctypes
                hwnd = int(ctypes.windll.user32.GetForegroundWindow())
            except Exception:  # noqa: BLE001
                hwnd = 0
        if hwnd:
            return InteractiveBrowserBrokerCredential(parent_window_handle=hwnd, **common)
        logger.warning("auth: no parent HWND available; falling back to browser credential.")
    return InteractiveBrowserCredential(**common)


# ---- projects store ------------------------------------------------------
#
# Multi-project support. Each project is a separate workspace (its own
# Foundry session, its own $HOME sandbox on the agent side). The client owns
# the list; the agent learns which project is active via a one-line preamble
# the client prepends to every outbound prompt.

_PROJECTS_PATH = _APP_HOME / "projects.json"


def _gen_project_id() -> str:
    import uuid
    return f"p-{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _load_projects() -> dict[str, Any]:
    if not _PROJECTS_PATH.exists():
        return {"active": None, "projects": {}}
    try:
        return json.loads(_PROJECTS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"active": None, "projects": {}}


def _save_projects(data: dict[str, Any]) -> None:
    _PROJECTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PROJECTS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(_PROJECTS_PATH)


# ---------- agent-sandbox readers (state-from-disk; no model turn) ----------

def _read_project_log(pid: str) -> dict[str, Any] | None:
    """Read `<AGENT_HOME>/projects/<pid>/project_log.json` from disk."""
    p = _AGENT_HOME / "projects" / pid / "project_log.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _dashboard_from_log(log: dict[str, Any]) -> dict[str, Any]:
    """Mirror of `dashboard_payload` tool: derive UI dashboard from project_log.

    Kept here so the client can repaint without a model turn. The shape must
    match what the agent's `dashboard_payload()` tool returns — see
    `agent/src/charter_agent/skills/sow_response/tools.py`.
    """
    tasks = log.get("tasks", [])
    submitted_states = {"submitted", "submitted_with_gaps"}
    section_status_map = {"in_progress": "inprogress", "overdue": "atrisk"}
    sections: list[dict[str, Any]] = []
    earliest_unmet_due: str | None = None
    exceptions: list[dict[str, Any]] = []
    for t in tasks:
        raw = t.get("status", "assigned")
        ui_status = section_status_map.get(raw, raw)
        last_signal = "awaiting reply"
        subs = t.get("submissions", [])
        if subs:
            last_signal = subs[-1].get("summary") or "reply received"
        elif (t.get("kickoff_sent") or {}).get("at"):
            last_signal = f"kicked off via {t['kickoff_sent']['channel']}"
        sections.append({
            "task_id": t.get("task_id"),
            "title": t.get("title"),
            "owner": t.get("owner_display_name") or t.get("owner_upn"),
            "status": ui_status,
            "due_at": t.get("due_at") or "",
            "last_signal": last_signal,
        })
        if raw not in submitted_states and t.get("due_at"):
            if earliest_unmet_due is None or t["due_at"] < earliest_unmet_due:
                earliest_unmet_due = t["due_at"]
        if raw == "overdue":
            exceptions.append({
                "kind": "atrisk",
                "title": t.get("task_id"),
                "body": f"{t.get('owner_display_name', t.get('owner_upn'))} is past due.",
            })
    order = (log.get("consolidation_rules") or {}).get("section_order") or []
    if order:
        idx = {tid: i for i, tid in enumerate(order)}
        sections.sort(key=lambda s: idx.get(s["task_id"], len(idx)))
    submitted = sum(1 for t in tasks if t.get("status") in submitted_states)
    return {
        "kind": "dashboard",
        "project": log.get("project_id"),
        "customer": log.get("customer_name"),
        "status": log.get("status"),
        "summary": "",
        "due": earliest_unmet_due or "",
        "progress": {"submitted": submitted, "total": len(tasks)},
        "sections": sections,
        "exceptions": exceptions,
        "deliverable_url": (log.get("deliverable") or {}).get("url", ""),
    }


def _read_activity_tail(limit: int = 200) -> list[dict[str, Any]]:
    """Return the last `limit` rows from `<AGENT_HOME>/activity.json` (NDJSON)."""
    p = _AGENT_HOME / "activity.json"
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


def _project_view(pid: str) -> dict[str, Any]:
    """Build the disk-derived view for a project (dashboard + recent audit)."""
    log = _read_project_log(pid)
    return {
        "project_id": pid,
        "dashboard": _dashboard_from_log(log) if log else None,
        "activity": _read_activity_tail(200),
    }


class Bridge:
    """JS-callable surface exposed to the WebView via pywebview's js_api."""

    def __init__(self, *, local_url: str, hosted_url: str, initial_mode: str) -> None:
        self.window: webview.Window | None = None
        self.token: str | None = None
        self.token_expires_at: float = 0.0
        self.user_name: str | None = None
        self._credential: Any = None
        self._record_saved: bool = _AUTH_RECORD_PATH.exists()
        self.endpoints = {"local": local_url, "hosted": hosted_url}
        self.mode: str = initial_mode if initial_mode in self.endpoints else "hosted"
        self._lock = threading.Lock()

        # Load projects from disk; create a starter project if empty so the
        # UI has something to show. Each project carries its own Foundry
        # session_id and previous_response_id so switching never crosses
        # conversation history between RFPs.
        data = _load_projects()
        if not data.get("projects"):
            seed = os.environ.get("AGENT_PROJECT_ID") or _gen_project_id()
            data = {
                "active": seed,
                "projects": {
                    seed: {
                        "label": "New project",
                        "customer_name": "",
                        "session_id": None,
                        "previous_response_id": None,
                        "is_new": True,
                        "created_at": _now_iso(),
                        "last_used_at": _now_iso(),
                    }
                },
            }
            _save_projects(data)
        elif not data.get("active") or data["active"] not in data["projects"]:
            data["active"] = next(iter(data["projects"]))
            _save_projects(data)
        self._projects_data = data

    # ---------- project state shortcuts ----------

    @property
    def project_id(self) -> str:
        return self._projects_data["active"]

    @property
    def _current(self) -> dict[str, Any]:
        return self._projects_data["projects"][self.project_id]

    @property
    def session_id(self) -> str | None:
        return self._current.get("session_id")

    @session_id.setter
    def session_id(self, value: str | None) -> None:
        if self._current.get("session_id") != value:
            self._current["session_id"] = value
            _save_projects(self._projects_data)

    @property
    def previous_response_id(self) -> str | None:
        return self._current.get("previous_response_id")

    @previous_response_id.setter
    def previous_response_id(self, value: str | None) -> None:
        if self._current.get("previous_response_id") != value:
            self._current["previous_response_id"] = value
            _save_projects(self._projects_data)

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
            "projects": self._projects_payload(),
            "view": _project_view(self.project_id),
        }

    # ---------- projects API ----------

    def project_view(self, project_id: str = "") -> dict:
        """Reload the disk-derived view (dashboard + activity) for a project.

        Used by the UI to restore state on switch / boot without a model turn.
        """
        pid = project_id or self.project_id
        return {"ok": True, **_project_view(pid)}

    def _projects_payload(self) -> dict[str, Any]:
        ordered = sorted(
            self._projects_data["projects"].items(),
            key=lambda kv: kv[1].get("last_used_at") or kv[1].get("created_at") or "",
            reverse=True,
        )
        return {
            "active": self.project_id,
            "list": [
                {
                    "id": pid,
                    "label": p.get("label") or "New project",
                    "customer_name": p.get("customer_name") or "",
                    "is_new": bool(p.get("is_new")),
                    "last_used_at": p.get("last_used_at") or "",
                }
                for pid, p in ordered
            ],
        }

    def list_projects(self) -> dict:
        return {"ok": True, **self._projects_payload()}

    def new_project(self, label: str = "") -> dict:
        pid = _gen_project_id()
        self._projects_data["projects"][pid] = {
            "label": label.strip() or "New project",
            "customer_name": "",
            "session_id": None,
            "previous_response_id": None,
            "is_new": True,
            "created_at": _now_iso(),
            "last_used_at": _now_iso(),
        }
        self._projects_data["active"] = pid
        _save_projects(self._projects_data)
        return {"ok": True, "active": pid, "view": _project_view(pid), **self._projects_payload()}

    def switch_project(self, project_id: str) -> dict:
        if project_id not in self._projects_data["projects"]:
            return {"ok": False, "error": f"unknown project_id: {project_id}"}
        self._projects_data["active"] = project_id
        self._current["last_used_at"] = _now_iso()
        _save_projects(self._projects_data)
        return {
            "ok": True,
            "active": project_id,
            "session_id": self.session_id,
            "previous_response_id": self.previous_response_id,
            "view": _project_view(project_id),
            **self._projects_payload(),
        }

    def rename_project(self, project_id: str, label: str) -> dict:
        if project_id not in self._projects_data["projects"]:
            return {"ok": False, "error": f"unknown project_id: {project_id}"}
        self._projects_data["projects"][project_id]["label"] = label.strip() or "New project"
        _save_projects(self._projects_data)
        return {"ok": True, **self._projects_payload()}

    def delete_project(self, project_id: str) -> dict:
        projects = self._projects_data["projects"]
        if project_id not in projects:
            return {"ok": False, "error": f"unknown project_id: {project_id}"}
        del projects[project_id]
        if not projects:
            pid = _gen_project_id()
            projects[pid] = {
                "label": "New project",
                "customer_name": "",
                "session_id": None,
                "previous_response_id": None,
                "is_new": True,
                "created_at": _now_iso(),
                "last_used_at": _now_iso(),
            }
            self._projects_data["active"] = pid
        elif self._projects_data["active"] == project_id:
            self._projects_data["active"] = next(iter(projects))
        _save_projects(self._projects_data)
        return {"ok": True, "active": self._projects_data["active"], **self._projects_payload()}

    def set_mode(self, mode: str) -> dict:
        if mode not in self.endpoints:
            return {"ok": False, "error": f"unknown mode: {mode}"}
        if not self.endpoints[mode]:
            return {"ok": False, "error": f"endpoint for mode={mode!r} is not configured"}
        self.mode = mode
        # Wipe per-project session state across the board — endpoint change
        # invalidates server-side session ids.
        for p in self._projects_data["projects"].values():
            p["session_id"] = None
            p["previous_response_id"] = None
        _save_projects(self._projects_data)
        return {"ok": True, "mode": mode, "agent_url": self.endpoints[mode]}

    def login(self) -> dict:
        try:
            if self._credential is None:
                self._credential = _build_credential(self._parent_hwnd())
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
        """Forget the current project's server-side session, keep the project itself.

        Use this when the host endpoint or session_id has drifted; the next
        message will land in a fresh Foundry session under the same project_id.
        """
        self.session_id = None
        self.previous_response_id = None
        return {"ok": True, "project_id": self.project_id}

    # ---------- chat ----------

    def send(self, prompt: str) -> dict:
        url = self.endpoints.get(self.mode)
        if not url:
            return {"ok": False, "error": f"endpoint for mode={self.mode!r} is not configured."}
        if not self.token or time.time() >= self.token_expires_at - 60:
            r = self.login()
            if not r.get("ok"):
                return r
        # Mark the project as 'used' so the host knows whether to create-or-resume.
        pid = self.project_id
        is_new = bool(self._current.get("is_new"))
        if is_new:
            self._current["is_new"] = False
        self._current["last_used_at"] = _now_iso()
        _save_projects(self._projects_data)
        preamble = f"[charter-agent-context: project_id={pid} is_new={'true' if is_new else 'false'}]\n"
        threading.Thread(target=self._run_turn, args=(prompt, preamble + prompt, url), daemon=True).start()
        return {"ok": True}

    # ---------- internals ----------

    def _parent_hwnd(self) -> int:
        """Return the HWND of the pywebview window for WAM anchoring.

        pywebview's EdgeChromium backend exposes the host WinForms form as
        `window.native`. Its `.Handle` attribute is the Win32 HWND. Returns
        0 if the window is not yet realized or the backend doesn't expose
        a handle — callers should fall back to `GetForegroundWindow()`.
        """
        win = self.window
        if win is None:
            return 0
        native = getattr(win, "native", None)
        if native is None:
            return 0
        handle = getattr(native, "Handle", None)
        try:
            return int(handle) if handle is not None else 0
        except (TypeError, ValueError):
            return 0

    def _emit(self, event: str, payload: dict | None = None) -> None:
        if not self.window:
            return
        msg = json.dumps({"event": event, "payload": payload or {}})
        try:
            self.window.evaluate_js(f"window.onAgentEvent && window.onAgentEvent({msg})")
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] evaluate_js failed: {e}", file=sys.stderr)

    def _run_turn(self, display_prompt: str, wire_prompt: str, url: str) -> None:
        with self._lock:
            self._emit("turn.start", {"prompt": display_prompt, "session_id": self.session_id, "mode": self.mode, "project_id": self.project_id})
            try:
                self._post_one(wire_prompt, url=url, previous_response_id=self.previous_response_id)
            except Exception as e:  # noqa: BLE001
                self._emit("turn.error", {"error": str(e)})

    def _post_one(self, prompt: str, *, url: str, previous_response_id: str | None) -> None:
        # NB: do NOT set store=False on the host call — the Responses host server
        # uses its own transcript store to resolve previous_response_id, and
        # opting out wipes the model's memory of prior turns (RFP grounding,
        # tool outputs, etc.). The upstream-model `store` flag is pinned in
        # runtime/foundry_host.py, which is the right place for it.
        body: dict[str, Any] = {"input": prompt, "stream": True}
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
                            self._emit("tool.args", {"name": current_tool["name"], "args": current_tool["args"]})
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
        # Learn the customer name + status from the dashboard so the sidebar label is meaningful.
        if dashboard:
            customer = dashboard.get("customer") or ""
            if customer and customer != self._current.get("customer_name"):
                self._current["customer_name"] = customer
                if (self._current.get("label") or "").lower() in ("", "new project"):
                    self._current["label"] = customer
                _save_projects(self._projects_data)
                self._emit("projects.update", self._projects_payload())
        self._emit("turn.complete", {
            "response_id": response_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "text": final_text,
            "dashboard": dashboard,
        })
        # Refresh the disk-derived view (activity audit + canonical dashboard
        # from project_log.json) so the UI matches the agent's source of truth.
        self._emit("view.update", _project_view(self.project_id))


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
