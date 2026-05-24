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
import shutil
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
DEFAULT_HOSTED_AGENT_NAME = "charter-agent"
TENANT_ID = os.environ.get("SPIKE_TENANT_ID") or None
SCOPE = "https://ai.azure.com/.default"
HTTP_TIMEOUT = httpx.Timeout(600.0, connect=30.0)


def _load_agent_env() -> None:
    """Best-effort load of `<repo>/agent/.env` so the client picks up FOUNDRY_PROJECT_ENDPOINT."""
    env_path = pathlib.Path(__file__).resolve().parent.parent / "agent" / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:  # noqa: BLE001
        pass


def _resolve_hosted_url() -> str:
    """Resolve hosted /responses URL from explicit env or construct from FOUNDRY_PROJECT_ENDPOINT."""
    explicit = os.environ.get("AGENT_ENDPOINT_HOSTED") or os.environ.get("AGENT_RESPONSES_URL")
    if explicit:
        return explicit.strip()
    project_endpoint = (os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or "").strip().rstrip("/")
    if not project_endpoint:
        return ""
    agent_name = os.environ.get("AGENT_NAME") or DEFAULT_HOSTED_AGENT_NAME
    return f"{project_endpoint}/agents/{agent_name}/endpoint/protocols/openai/responses?api-version=v1"

UI_HTML = pathlib.Path(__file__).with_name("ui.html")
ASSETS_DIR = pathlib.Path(__file__).with_name("assets")
APP_ICON_ICO = ASSETS_DIR / "app_icon.ico"
APP_ICON_PNG = ASSETS_DIR / "app_icon.png"
WINDOW_TITLE = "Project Charter Desktop Agent"

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


def _build_credential(parent_hwnd: int = 0, *, silent_only: bool = False) -> Any:
    """Build the interactive credential used for sign-in.

    Prefers `InteractiveBrowserBrokerCredential` (Windows account picker via
    WAM) when `azure-identity-broker` is installed; otherwise falls back to
    `InteractiveBrowserCredential` (system browser). Both reuse a persisted
    `AuthenticationRecord` to make repeat launches silent.

    `parent_hwnd` should be the HWND of the foreground app window. WAM
    cancels the request immediately when the handle is 0/invalid, so we
    fall back to `GetForegroundWindow()` and then to the browser credential
    when no usable handle is available.

    When `silent_only=True`, the credential is built with
    `disable_automatic_authentication=True` so `get_token` raises rather than
    popping a UI prompt — used for startup auto sign-in.
    """
    record = _load_record()
    cache_opts = TokenCachePersistenceOptions(name=AUTH_CACHE_NAME)
    common: dict[str, Any] = {
        "cache_persistence_options": cache_opts,
        "authentication_record": record,
    }
    if TENANT_ID:
        common["tenant_id"] = TENANT_ID
    if silent_only:
        common["disable_automatic_authentication"] = True

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
# Client-side cache of the latest dashboard payload per (mode, project_id).
# Used to restore the dashboard on app restart for hosted mode, where the
# agent's $HOME lives in a Foundry microVM the client can't read directly.
_VIEW_CACHE_PATH = _APP_HOME / "view_cache.json"


def _load_view_cache() -> dict[str, Any]:
    if not _VIEW_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_VIEW_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_view_cache(cache: dict[str, Any]) -> None:
    _VIEW_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _VIEW_CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    tmp.replace(_VIEW_CACHE_PATH)


def _gen_project_id() -> str:
    import uuid
    return f"p-{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


_MODES = ("local", "hosted")


def _empty_projects_store() -> dict[str, Any]:
    return {
        "active": {m: None for m in _MODES},
        "projects": {m: {} for m in _MODES},
    }


def _migrate_projects_store(data: dict[str, Any]) -> dict[str, Any]:
    """Promote a pre-per-mode flat store to the per-mode shape.

    Old shape: {"active": "p-xxx", "projects": {"p-xxx": {...}}}
    New shape: {"active": {"local": "p-xxx", "hosted": null}, "projects": {"local": {...}, "hosted": {}}}
    Existing projects are bucketed under `local` since that's where dev workflows live.
    """
    projects = data.get("projects") or {}
    active = data.get("active")
    if isinstance(active, dict) and isinstance(projects, dict) and "local" in projects and "hosted" in projects:
        # Already per-mode; just ensure all keys present.
        for m in _MODES:
            projects.setdefault(m, {})
            active.setdefault(m, None)
        return {"active": active, "projects": projects}
    # Flat → per-mode migration.
    return {
        "active": {"local": active if isinstance(active, str) else None, "hosted": None},
        "projects": {"local": projects if isinstance(projects, dict) else {}, "hosted": {}},
    }


def _load_projects() -> dict[str, Any]:
    if not _PROJECTS_PATH.exists():
        return _empty_projects_store()
    try:
        raw = json.loads(_PROJECTS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return _empty_projects_store()
    return _migrate_projects_store(raw)


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
        "skill": log.get("skill"),
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


def _project_view(pid: str, *, mode: str | None = None) -> dict[str, Any]:
    """Build the view for a project (dashboard + recent audit).

    Prefers the agent's on-disk state under `<AGENT_HOME>/projects/<pid>/`,
    which is the source of truth in local mode. For hosted mode the agent's
    `$HOME` lives in a Foundry microVM we can't read directly, so we fall
    back to the client-side `view_cache.json` populated from the most recent
    `turn.complete` dashboard payload.
    """
    log = _read_project_log(pid)
    dashboard = _dashboard_from_log(log) if log else None
    activity = _read_activity_tail(200)
    if dashboard is None and mode:
        cache = _load_view_cache().get(f"{mode}/{pid}") or {}
        cached_dash = cache.get("dashboard")
        if cached_dash:
            # Tag the cached payload so the UI can show a "stale" indicator
            # and so the boot handler can decide to auto-refresh.
            cached_dash = {**cached_dash, "from_cache": True, "saved_at": cache.get("saved_at") or ""}
            dashboard = cached_dash
        if not activity:
            activity = cache.get("activity") or []
    return {
        "project_id": pid,
        "dashboard": dashboard,
        "activity": activity,
    }


class Bridge:
    """JS-callable surface exposed to the WebView via pywebview's js_api."""

    def __init__(self, *, local_url: str, hosted_url: str, initial_mode: str) -> None:
        # Leading underscore is load-bearing: pywebview's js_api reflector (util.py
        # get_functions) descends into any non-underscore attribute and would walk
        # the WinForms Form -> AccessibilityObject -> Rectangle.Empty.Empty... loop
        # until Python's recursion limit aborts mid-injection and hangs the bridge.
        self._window: webview.Window | None = None
        self.token: str | None = None
        self.token_expires_at: float = 0.0
        self.user_name: str | None = None
        self._credential: Any = None
        self._record_saved: bool = _AUTH_RECORD_PATH.exists()
        self.endpoints = {"local": local_url, "hosted": hosted_url}
        self.mode: str = initial_mode if initial_mode in self.endpoints else "hosted"
        self._lock = threading.Lock()

        # Load projects from disk. Projects are scoped per endpoint mode
        # (local sandboxes vs hosted microVMs are different $HOMEs, so a
        # project_id created in one never resolves in the other). For the
        # CURRENT mode only, seed a starter project if none exists so the UI
        # has something to show. Each project carries its own session_id and
        # previous_response_id.
        data = _load_projects()
        self._projects_data = data
        self._ensure_seeded_for_mode(self.mode)

    # ---------- project state shortcuts ----------

    def _mode_projects(self) -> dict[str, Any]:
        return self._projects_data["projects"].setdefault(self.mode, {})

    def _sync_skill_from_view(self, view: dict[str, Any] | None) -> bool:
        """Promote the agent-declared `skill` from a view's dashboard onto the
        active project record. Returns True if the record changed (caller can
        decide whether to re-emit `projects.update`).

        The skill name is owned by the agent: the chosen skill writes it into
        `project_log.json` at kickoff. We mirror it client-side only so the
        sidebar can render the tag without doing a fresh disk read on every
        repaint. Never invented client-side.
        """
        if not view:
            return False
        dash = view.get("dashboard") or {}
        skill_name = (dash.get("skill") or "").strip()
        if not skill_name:
            return False
        if self._current.get("skill") == skill_name:
            return False
        self._current["skill"] = skill_name
        _save_projects(self._projects_data)
        return True

    def _ensure_seeded_for_mode(self, mode: str) -> None:
        projects = self._projects_data["projects"].setdefault(mode, {})
        active_map = self._projects_data["active"]
        if not projects:
            pid = (os.environ.get("AGENT_PROJECT_ID") if mode == "local" else None) or _gen_project_id()
            projects[pid] = {
                "label": "New project",
                "customer_name": "",
                "session_id": None,
                "previous_response_id": None,
                "is_new": True,
                "created_at": _now_iso(),
                "last_used_at": _now_iso(),
            }
            active_map[mode] = pid
            _save_projects(self._projects_data)
        elif not active_map.get(mode) or active_map[mode] not in projects:
            active_map[mode] = next(iter(projects))
            _save_projects(self._projects_data)

    @property
    def project_id(self) -> str:
        return self._projects_data["active"][self.mode]

    @property
    def _current(self) -> dict[str, Any]:
        return self._mode_projects()[self.project_id]

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
        print(f"[bridge] ready() called mode={self.mode!r} url={self.endpoints.get(self.mode, '')!r}", flush=True)
        view = _project_view(self.project_id, mode=self.mode)
        self._sync_skill_from_view(view)
        return {
            "endpoints": self.endpoints,
            "mode": self.mode,
            "agent_url": self.endpoints.get(self.mode, ""),
            "session_id": self.session_id,
            "tenant_id": TENANT_ID,
            "scope": SCOPE,
            "user_name": self.user_name,
            "has_record": self._record_saved,
            "projects": self._projects_payload(),
            "view": view,
        }

    def signin_silent(self) -> dict:
        """Attempt a non-interactive sign-in using the persisted AuthenticationRecord.

        Returns `{ok: True, user_name, expires_on}` if the cached refresh token
        produced a fresh access token without user interaction. Returns
        `{ok: False, ...}` if no record exists or the silent flow would require
        a popup (in which case the UI keeps the "signed out" state and the user
        must click Sign in).
        """
        if not self._record_saved:
            return {"ok": False, "error": "no saved authentication record"}
        try:
            silent_cred = _build_credential(self._parent_hwnd(), silent_only=True)
            tok = silent_cred.get_token(SCOPE)
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] signin_silent failed: {e}", flush=True)
            return {"ok": False, "error": str(e)}
        # Don't cache the silent-only credential into self._credential: a later
        # token refresh might genuinely need to pop the WAM picker, and the
        # silent-only flag would suppress that. Next login() will lazily build
        # a regular interactive credential.
        self.token = tok.token
        self.token_expires_at = float(tok.expires_on)
        self.user_name = _decode_jwt_name(tok.token)
        print(f"[bridge] signin_silent ok user={self.user_name!r}", flush=True)
        return {"ok": True, "expires_on": int(tok.expires_on), "user_name": self.user_name}

    # ---------- projects API ----------

    def project_view(self, project_id: str = "") -> dict:
        """Reload the disk-derived view (dashboard + activity) for a project.

        Used by the UI to restore state on switch / boot without a model turn.
        """
        pid = project_id or self.project_id
        return {"ok": True, **_project_view(pid, mode=self.mode)}

    def _projects_payload(self) -> dict[str, Any]:
        # Stable order: newest-created first. Clicking a project must not move
        # it in the list — that was disorienting. `last_used_at` still gets
        # bumped on every interaction for downstream diagnostics, but it no
        # longer drives sort order.
        ordered = sorted(
            self._mode_projects().items(),
            key=lambda kv: kv[1].get("created_at") or "",
            reverse=True,
        )
        return {
            "active": self.project_id,
            "list": [
                {
                    "id": pid,
                    "label": p.get("label") or "New project",
                    "customer_name": p.get("customer_name") or "",
                    "skill": p.get("skill") or "",
                    "is_new": bool(p.get("is_new")),
                    "created_at": p.get("created_at") or "",
                    "last_used_at": p.get("last_used_at") or "",
                }
                for pid, p in ordered
            ],
        }

    def list_projects(self) -> dict:
        return {"ok": True, **self._projects_payload()}

    def new_project(self, label: str = "") -> dict:
        pid = _gen_project_id()
        self._mode_projects()[pid] = {
            "label": label.strip() or "New project",
            "customer_name": "",
            "session_id": None,
            "previous_response_id": None,
            "is_new": True,
            "created_at": _now_iso(),
            "last_used_at": _now_iso(),
        }
        self._projects_data["active"][self.mode] = pid
        _save_projects(self._projects_data)
        return {"ok": True, "active": pid, "view": _project_view(pid, mode=self.mode), **self._projects_payload()}

    def switch_project(self, project_id: str) -> dict:
        projects = self._mode_projects()
        if project_id not in projects:
            return {"ok": False, "error": f"unknown project_id: {project_id}"}
        self._projects_data["active"][self.mode] = project_id
        self._current["last_used_at"] = _now_iso()
        _save_projects(self._projects_data)
        view = _project_view(project_id, mode=self.mode)
        self._sync_skill_from_view(view)
        return {
            "ok": True,
            "active": project_id,
            "session_id": self.session_id,
            "previous_response_id": self.previous_response_id,
            "view": view,
            **self._projects_payload(),
        }

    def rename_project(self, project_id: str, label: str) -> dict:
        projects = self._mode_projects()
        if project_id not in projects:
            return {"ok": False, "error": f"unknown project_id: {project_id}"}
        projects[project_id]["label"] = label.strip() or "New project"
        _save_projects(self._projects_data)
        return {"ok": True, **self._projects_payload()}

    def _delete_local_project_dir(self, project_id: str) -> None:
        """Best-effort rmtree of `<AGENT_HOME>/projects/<pid>/`."""
        target = (_AGENT_HOME / "projects" / project_id).resolve()
        try:
            base = (_AGENT_HOME / "projects").resolve()
            # Refuse to delete anything outside the agent's projects subtree.
            target.relative_to(base)
        except Exception as ex:  # noqa: BLE001
            print(f"[bridge] delete_local refused {project_id!r}: {ex}", flush=True)
            return
        if not target.is_dir():
            return
        try:
            shutil.rmtree(target)
            print(f"[bridge] delete_local removed {target}", flush=True)
        except Exception as ex:  # noqa: BLE001
            print(f"[bridge] delete_local failed for {target}: {ex}", flush=True)

    def _delete_hosted_session(self, session_id: str) -> None:
        """Best-effort DELETE of the Foundry agentserver session microVM.

        The agentserver exposes session lifecycle at
        `<agent-endpoint-base>/agent-sessions/{id}?api-version=v1`. If that
        path doesn't match (preview API drift), we swallow the error — the
        microVM will idle-reap in <=30 days regardless. Pointer cleanup in
        `delete_project` does not depend on this call succeeding.
        """
        url = self.endpoints.get("hosted") or ""
        if not url:
            return
        # Strip `/protocols/openai/responses[?...]` to get the agent endpoint base.
        base = re.sub(r"/protocols/openai/responses(\?.*)?$", "", url)
        token = self.token
        if not token:
            print(f"[bridge] delete_hosted skipped (no token) session={session_id}", flush=True)
            return
        candidates = [
            f"{base}/agent-sessions/{session_id}?api-version=v1",
            f"{base}/sessions/{session_id}?api-version=v1",
        ]
        for candidate in candidates:
            try:
                with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                    resp = client.delete(
                        candidate,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                print(f"[bridge] delete_hosted {resp.status_code} {candidate}", flush=True)
                if resp.status_code in (200, 202, 204, 404):
                    return  # 404 = already gone, treat as success
            except Exception as ex:  # noqa: BLE001
                print(f"[bridge] delete_hosted error {candidate}: {ex}", flush=True)

    def delete_project(self, project_id: str) -> dict:
        projects = self._mode_projects()
        if project_id not in projects:
            return {"ok": False, "error": f"unknown project_id: {project_id}"}
        record = dict(projects[project_id])
        # Hard-delete agent-side state first; pointer removal happens regardless.
        if self.mode == "local":
            self._delete_local_project_dir(project_id)
        else:
            sid = record.get("session_id")
            if sid:
                self._delete_hosted_session(str(sid))
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
            self._projects_data["active"][self.mode] = pid
        elif self._projects_data["active"][self.mode] == project_id:
            self._projects_data["active"][self.mode] = next(iter(projects))
        _save_projects(self._projects_data)
        return {"ok": True, "active": self._projects_data["active"][self.mode], **self._projects_payload()}

    def set_mode(self, mode: str) -> dict:
        if mode not in self.endpoints:
            return {"ok": False, "error": f"unknown mode: {mode}"}
        if not self.endpoints[mode]:
            return {"ok": False, "error": f"endpoint for mode={mode!r} is not configured"}
        # session_id and previous_response_id are tied to the endpoint that
        # minted them; carrying them across a mode switch makes the new endpoint
        # reject the next request (unknown previous_response_id / session).
        self.session_id = None
        self.previous_response_id = None
        self.mode = mode
        self._ensure_seeded_for_mode(mode)
        return {
            "ok": True,
            "mode": mode,
            "agent_url": self.endpoints[mode],
            "projects": self._projects_payload(),
            "view": _project_view(self.project_id, mode=self.mode),
        }

    def login(self) -> dict:
        print("[bridge] login() called — invoking credential.get_token", flush=True)
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
        win = self._window
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
        if not self._window:
            return
        msg = json.dumps({"event": event, "payload": payload or {}})
        try:
            self._window.evaluate_js(f"window.onAgentEvent && window.onAgentEvent({msg})")
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

        print(f"[bridge] POST {url} session={self.session_id} prev_resp={previous_response_id}", flush=True)
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            with client.stream("POST", url, json=body, headers=headers) as resp:
                print(f"[bridge] <- status={resp.status_code} ct={resp.headers.get('content-type')}", flush=True)
                if resp.status_code >= 400:
                    err = resp.read().decode("utf-8", errors="replace")
                    print(f"[bridge] error body: {err[:2000]}", flush=True)
                    self._emit("turn.error", {"error": f"HTTP {resp.status_code}: {err[:2000]}"})
                    return
                event_count = 0
                for evt in _iter_sse_events(resp):
                    event_count += 1
                    data = evt["data"]
                    etype_dbg = (data.get("type") if isinstance(data, dict) else None) or evt.get("event")
                    if event_count <= 5 or event_count % 25 == 0:
                        print(f"[bridge] evt#{event_count} type={etype_dbg}", flush=True)
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

        print(f"[bridge] stream done: events={event_count} response_id={response_id} text_parts={len(completed_text_parts)} consent={consent_payload is not None}", flush=True)
        final_text = "\n".join(completed_text_parts).strip()
        if response_id:
            self.previous_response_id = response_id

        # The hosted Responses server uses an in-memory transcript store; a
        # container restart silently invalidates every saved
        # previous_response_id and the next request comes back 200 with an
        # empty completion (no tool calls, no text). Detect that and retry
        # once from a clean slate so the user sees output instead of nothing.
        #
        # IMPORTANT: only drop previous_response_id, NOT agent_session_id.
        # The session id keys the persistent Foundry microVM + $HOME (charter,
        # project_log, etc.) and survives container restarts within the
        # session's lifetime. previous_response_id keys the in-memory
        # transcript and is the thing that just rolled.
        if (
            previous_response_id
            and not consent_payload
            and not final_text
            and event_count <= 4
        ):
            print("[bridge] empty completion w/ stale prev_resp; retrying without it (keeping session)", flush=True)
            self.previous_response_id = None
            self._post_one(prompt, url=url, previous_response_id=None)
            return

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
        # Learn the customer name + skill from the dashboard so the sidebar reflects
        # what the agent actually decided. The skill name is the agent's declaration
        # (written into project_log.json by the chosen skill's kickoff tool); we never
        # default it client-side.
        if dashboard:
            mutated = False
            customer = dashboard.get("customer") or ""
            if customer and customer != self._current.get("customer_name"):
                self._current["customer_name"] = customer
                if (self._current.get("label") or "").lower() in ("", "new project"):
                    self._current["label"] = customer
                mutated = True
            skill_name = dashboard.get("skill") or ""
            if skill_name and skill_name != self._current.get("skill"):
                self._current["skill"] = skill_name
                mutated = True
            if mutated:
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
        view = _project_view(self.project_id, mode=self.mode)
        if self._sync_skill_from_view(view):
            self._emit("projects.update", self._projects_payload())
        # If this turn produced a fresh dashboard, treat it as live state —
        # overrides whatever stale cache `_project_view` may have returned for
        # hosted mode, and strips any from_cache/saved_at markers.
        if dashboard:
            live_dash = {k: v for k, v in dashboard.items() if k not in ("from_cache", "saved_at")}
            view["dashboard"] = live_dash
        # Persist the latest dashboard + activity to the client-side cache so
        # the UI can restore them on app restart (essential for hosted mode
        # where the agent's $HOME is inside an inaccessible Foundry microVM).
        cache_dashboard = view.get("dashboard")
        if cache_dashboard:
            # Don't store the staleness markers — saved_at is the cache's own field.
            cache_dashboard = {k: v for k, v in cache_dashboard.items() if k not in ("from_cache", "saved_at")}
        if cache_dashboard or view.get("activity"):
            try:
                cache = _load_view_cache()
                cache[f"{self.mode}/{self.project_id}"] = {
                    "dashboard": cache_dashboard,
                    "activity": view.get("activity") or [],
                    "saved_at": _now_iso(),
                }
                _save_view_cache(cache)
            except Exception as ex:  # noqa: BLE001
                logger.debug("view cache write failed: %s", ex)
        self._emit("view.update", view)


def _set_taskbar_icon(hwnd: int) -> None:
    """Override the default pythonw.exe taskbar/title-bar icon with app_icon.ico."""
    if not hwnd or sys.platform != "win32" or not APP_ICON_ICO.exists():
        logger.info("icon: skipped (hwnd=%s, exists=%s)", hwnd, APP_ICON_ICO.exists())
        return
    try:
        user32 = ctypes.windll.user32
        # Declare 64-bit-safe signatures so HICON handles aren't truncated.
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
        ]
        WM_SETICON = 0x0080
        ICON_BIG, ICON_SMALL = 1, 0
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        ico = str(APP_ICON_ICO)
        hicon_big = user32.LoadImageW(None, ico, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        hicon_small = user32.LoadImageW(None, ico, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        print(f"[icon] hwnd=0x{hwnd:x} big={hicon_big} small={hicon_small} ico={ico}", flush=True)
        logger.info("icon: hwnd=0x%x big=%s small=%s file=%s", hwnd, hicon_big, hicon_small, ico)
        if hicon_big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
        if hicon_small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
    except Exception as ex:  # noqa: BLE001
        logger.warning("taskbar icon set failed: %s", ex)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["local", "hosted"], default=os.environ.get("AGENT_ENDPOINT_MODE", "hosted"))
    p.add_argument("--local-url", default=os.environ.get("AGENT_ENDPOINT_LOCAL", DEFAULT_LOCAL))
    p.add_argument(
        "--hosted-url",
        default=None,
        help="URL of the deployed Foundry-hosted agent's /responses endpoint. "
             "Defaults to AGENT_ENDPOINT_HOSTED/AGENT_RESPONSES_URL, else constructed from FOUNDRY_PROJECT_ENDPOINT.",
    )
    p.add_argument("--debug", action="store_true", help="Open WebView2 DevTools (right-click → Inspect) and verbose logging.")
    args = p.parse_args()
    _load_agent_env()
    if not args.hosted_url:
        args.hosted_url = _resolve_hosted_url()

    if args.mode == "hosted" and not args.hosted_url:
        print("[warn] AGENT_ENDPOINT_HOSTED not set; starting in 'local' mode.", file=sys.stderr)
        args.mode = "local"

    if not UI_HTML.exists():
        print(f"!! missing ui.html at {UI_HTML}", file=sys.stderr)
        return 1

    bridge = Bridge(local_url=args.local_url, hosted_url=args.hosted_url, initial_mode=args.mode)

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Microsoft.WorkIQ.CharterAgent.Desktop"
            )
        except Exception as ex:  # noqa: BLE001
            logger.debug("AppUserModelID set failed: %s", ex)

    icon_path = APP_ICON_PNG if APP_ICON_PNG.exists() else (APP_ICON_ICO if APP_ICON_ICO.exists() else None)
    window = webview.create_window(
        WINDOW_TITLE,
        url=str(UI_HTML),
        js_api=bridge,
        width=1280,
        height=860,
        min_size=(960, 640),
    )
    bridge._window = window

    def _on_started() -> None:
        if sys.platform != "win32":
            return

        def _worker() -> None:
            try:
                user32 = ctypes.windll.user32
                user32.FindWindowW.restype = ctypes.c_void_p
                user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
                hwnd = 0
                for _ in range(40):  # up to ~4s
                    hwnd = user32.FindWindowW(None, WINDOW_TITLE) or 0
                    if hwnd:
                        break
                    time.sleep(0.1)
                print(f"[startup] hwnd=0x{hwnd:x} title={WINDOW_TITLE!r}", flush=True)
                _set_taskbar_icon(int(hwnd))
            except Exception as ex:  # noqa: BLE001
                print(f"[startup] hook failed: {ex}", flush=True)

        threading.Thread(target=_worker, name="taskbar-icon", daemon=True).start()

    webview.start(_on_started, gui="edgechromium", debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
