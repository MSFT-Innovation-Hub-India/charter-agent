"""The ``Bridge`` class — the JS-callable surface exposed to the WebView.

Owns sign-in, the per-mode project store, both transports (SDK default for
hosted, raw-httpx for local + fallback), and the shared post-stream tail. The
SDK transport (``_post_one_sdk`` / ``_ensure_session_sdk`` / the
``_BridgeTokenCredential`` shim) is preserved byte-for-byte from the tested
single-file client.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import webbrowser
from typing import Any

import httpx
import webview

from .auth import (
    _BridgeTokenCredential,
    _build_credential,
    _decode_jwt_name,
    _decode_jwt_oid,
    _decode_jwt_upn,
    _save_record,
)
from .config import (
    DEFAULT_HOSTED_AGENT_NAME,
    HTTP_TIMEOUT,
    SCOPE,
    TENANT_ID,
    AIProjectClient,
    VersionRefIndicator,
    _AGENT_HOME,
    _AUTH_RECORD_PATH,
    _POLL_CHECK_PROMPT,
    _POLL_INTERVAL_MINS,
    _resolve_project_endpoint,
    _SDK_AVAILABLE,
    logger,
)
from .notifications import _notify
from .protocol import (
    _extract_consent_url,
    _iter_sse_events,
    _maybe_extract_dashboard,
    _sdk_event_to_dict,
)
from .storage import (
    _diff_sections,
    _gen_project_id,
    _load_projects,
    _load_view_cache,
    _log_session,
    _now_iso,
    _project_view,
    _save_projects,
    _save_transcript_turn,
    _save_view_cache,
    _transcript_path,
)


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
        self.user_upn: str | None = None
        self.user_oid: str | None = None
        self._credential: Any = None
        self._record_saved: bool = _AUTH_RECORD_PATH.exists()
        # Foundry SDK client handles (hosted mode). Built lazily on first use so
        # we have a signed-in user bearer to wrap. Cached for the process life.
        self._project_client: Any = None
        self._openai_client: Any = None
        self.endpoints = {"local": local_url, "hosted": hosted_url}
        self.mode: str = initial_mode if initial_mode in self.endpoints else "hosted"
        self._lock = threading.Lock()
        self._last_response_at: float = 0.0  # epoch time of last completed turn
        # Per-project task-status snapshots used by the auto-poller to detect
        # changes between cycles. Shape: {pid: {task_id: status_string}}.
        # Populated from the dashboard payload on every turn completion.
        self._section_snapshots: dict[str, dict[str, str]] = {}

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
                "skill": "general",
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
        logger.debug("[bridge] ready() called mode=%r url=%r", self.mode, self.endpoints.get(self.mode, ""))
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
            "user_upn": self.user_upn,
            "user_oid": self.user_oid,
            "has_record": self._record_saved,
            "projects": self._projects_payload(),
            "view": view,
            "poll_interval_mins": _POLL_INTERVAL_MINS,
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
            logger.debug("[bridge] signin_silent failed: %s", e)
            return {"ok": False, "error": str(e)}
        # Don't cache the silent-only credential into self._credential: a later
        # token refresh might genuinely need to pop the WAM picker, and the
        # silent-only flag would suppress that. Next login() will lazily build
        # a regular interactive credential.
        self.token = tok.token
        self.token_expires_at = float(tok.expires_on)
        self.user_name = _decode_jwt_name(tok.token)
        self.user_upn = _decode_jwt_upn(tok.token)
        self.user_oid = _decode_jwt_oid(tok.token)
        logger.debug("[bridge] signin_silent ok user=%r upn=%r oid=%r", self.user_name, self.user_upn, self.user_oid)
        return {"ok": True, "expires_on": int(tok.expires_on), "user_name": self.user_name, "user_upn": self.user_upn, "user_oid": self.user_oid}

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
            "skill": "general",
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
            logger.warning("[bridge] delete_local refused %r: %s", project_id, ex)
            return
        if not target.is_dir():
            return
        try:
            shutil.rmtree(target)
            logger.debug("[bridge] delete_local removed %s", target)
        except Exception as ex:  # noqa: BLE001
            logger.warning("[bridge] delete_local failed for %s: %s", target, ex)

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
        token = self.token
        if not token:
            logger.debug("[bridge] delete_hosted skipped (no token) session=%s", session_id)
            return
        # Preferred path: the SDK's typed delete_session, when the SDK is
        # available and a project endpoint resolves. This is the idiomatic
        # counterpart of the create_session used to mint the session.
        if (
            _SDK_AVAILABLE
            and os.environ.get("CHARTER_CLIENT_TRANSPORT", "sdk").strip().lower() != "legacy"
            and _resolve_project_endpoint()
        ):
            try:
                agent_name = os.environ.get("AGENT_NAME") or DEFAULT_HOSTED_AGENT_NAME
                self._get_project_client().beta.agents.delete_session(
                    agent_name=agent_name, agent_session_id=session_id
                )
                logger.debug("[sdk] delete_session ok session=%s", session_id)
                return
            except Exception as ex:  # noqa: BLE001
                logger.warning("[sdk] delete_session failed (%s); falling back to REST", ex)
        # Fallback: best-effort REST DELETE against the agentserver session path.
        # Strip `/protocols/openai/responses[?...]` to get the agent endpoint base.
        base = re.sub(r"/protocols/openai/responses(\?.*)?$", "", url)
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
                logger.debug("[bridge] delete_hosted %d %s", resp.status_code, candidate)
                if resp.status_code in (200, 202, 204, 404):
                    return  # 404 = already gone, treat as success
            except Exception as ex:  # noqa: BLE001
                logger.warning("[bridge] delete_hosted error %s: %s", candidate, ex)

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
        # Purge the client-side view cache for this project so a re-created
        # project with the same id can't inherit stale tiles/activity.
        try:
            cache = _load_view_cache()
            key = f"{self.mode}/{project_id}"
            if key in cache:
                del cache[key]
                _save_view_cache(cache)
        except Exception as ex:  # noqa: BLE001
            logger.debug("view_cache purge failed for %s: %s", project_id, ex)
        try:
            tp = _transcript_path(self.mode, project_id)
            if tp.exists():
                tp.unlink()
        except Exception as ex:  # noqa: BLE001
            logger.debug("transcript purge failed for %s: %s", project_id, ex)
        del projects[project_id]
        if not projects:
            pid = _gen_project_id()
            projects[pid] = {
                "label": "New project",
                "customer_name": "",
                "skill": "general",
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
        # Projects are scoped per-mode: local projects carry local sessions,
        # hosted projects carry hosted sessions.  Switching modes does NOT mean
        # we should clear the other mode's sessions — each lives in its own
        # project record and must survive the switch so "Run now" still routes
        # back to the correct Foundry microVM.  The old "clear on switch" logic
        # was written before per-mode projects existed; now it's just harmful.
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
        logger.debug("[bridge] login() called — invoking credential.get_token")
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
        self.user_upn = _decode_jwt_upn(tok.token)
        self.user_oid = _decode_jwt_oid(tok.token)
        return {"ok": True, "expires_on": int(tok.expires_on), "user_name": self.user_name, "user_upn": self.user_upn, "user_oid": self.user_oid}

    def reset_session(self) -> dict:
        """Forget the current project's server-side session, keep the project itself.

        Use this when the host endpoint or session_id has drifted; the next
        message will land in a fresh Foundry session under the same project_id.
        """
        self.session_id = None
        self.previous_response_id = None
        return {"ok": True, "project_id": self.project_id}

    # ---------- chat ----------

    def send(self, prompt: str, skill: str = "") -> dict:
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
        # If the session has been idle longer than Foundry's 15-minute compute
        # deprovisioning window, a stale previous_response_id causes Foundry to
        # create a NEW session instead of restoring the existing VM — losing all
        # $HOME state. Clearing it here means Foundry gets only agent_session_id,
        # which correctly routes back to the saved VM without chaining history.
        _FOUNDRY_IDLE_SECS = 12 * 60  # 12 min — safely under the 15-min threshold
        if (
            self.previous_response_id
            and self._last_response_at > 0
            and (time.time() - self._last_response_at) > _FOUNDRY_IDLE_SECS
        ):
            logger.info("[bridge] clearing stale prev_resp after %ds idle", int(time.time() - self._last_response_at))
            self.previous_response_id = None

        preamble = f"[charter-agent-context: project_id={pid} is_new={'true' if is_new else 'false'}]\n"
        threading.Thread(target=self._run_turn, args=(prompt, preamble + prompt, url), daemon=True).start()
        return {"ok": True}

    def run_now(self) -> dict:
        """Manually trigger an immediate auto-check for the active project.

        Takes the same code path as the background poller — emits scheduler.tick,
        calls _run_auto_check (auto=True), and that path emits scheduler.done which
        triggers both the in-app toast and the OS notification. Unlike send(), this
        never creates a chat bubble.
        """
        if not self.token or time.time() >= self.token_expires_at - 60:
            r = self.login()
            if not r.get("ok"):
                return {"ok": False, "error": "Not signed in"}
        url = self.endpoints.get(self.mode)
        if not url:
            return {"ok": False, "error": "No endpoint configured"}
        pid = self.project_id
        project = self._mode_projects().get(pid or "")
        if not project or not project.get("session_id"):
            return {"ok": False, "error": "No active session — kick off the SOW first"}
        project_label = project.get("customer_name") or project.get("label") or pid
        self._emit("scheduler.tick", {
            "project_id": pid,
            "project_label": project_label,
            "next_in_mins": _POLL_INTERVAL_MINS,
        })
        _notify("Project Charter", f"Checking {project_label}…")
        _ctx = {
            "pid": pid,
            "session_id": project.get("session_id"),
            "project_dict": project,
        }
        self._run_auto_check(pid, project_label, _ctx=_ctx)
        return {"ok": True}

    def _run_auto_check(
        self,
        pid: str,
        project_label: str = "",
        *,
        _ctx: dict | None = None,
        done_event: threading.Event | None = None,
    ) -> None:
        """Trigger a background 'check and continue' turn for *pid*.

        _ctx carries the explicit project context (pid, session_id, project_dict)
        when checking a non-active project. When None, falls back to self._current.
        done_event is set when the turn completes, allowing the caller to sequence
        multiple project checks without concurrency.
        """
        url = self.endpoints.get(self.mode)
        if not url:
            logger.debug("[auto] no URL for mode=%r", self.mode)
            if done_event:
                done_event.set()
            return

        if _ctx is None:
            # Active-project case: apply the idle-timeout guard.
            _FOUNDRY_IDLE_SECS = 12 * 60
            if (
                self.previous_response_id
                and self._last_response_at > 0
                and (time.time() - self._last_response_at) > _FOUNDRY_IDLE_SECS
            ):
                logger.info("[auto] clearing stale prev_resp before auto-check (idle %ds)",
                            int(time.time() - self._last_response_at))
                self.previous_response_id = None
            _ctx = {
                "pid": pid,
                "session_id": self.session_id,
                "project_dict": self._current,
            }
        # Non-active projects always start with no previous_response_id: the idle
        # threshold certainly applies when we haven't used that project this session.
        _ctx.setdefault("prev_resp_id", None)

        prev_snap = dict(self._section_snapshots.get(pid, {}))
        preamble = f"[charter-agent-context: project_id={pid} is_new=false]\n"
        wire_prompt = preamble + _POLL_CHECK_PROMPT
        threading.Thread(
            target=self._run_turn,
            args=(_POLL_CHECK_PROMPT, wire_prompt, url),
            kwargs={
                "auto": True,
                "project_label": project_label,
                "prev_snap": prev_snap,
                "_ctx": _ctx,
                "done_event": done_event,
            },
            daemon=True,
        ).start()

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
            logger.warning("[bridge] evaluate_js failed: %s", e)

    def _run_turn(
        self,
        display_prompt: str,
        wire_prompt: str,
        url: str,
        *,
        auto: bool = False,
        project_label: str = "",
        prev_snap: dict[str, str] | None = None,
        _ctx: dict | None = None,
        done_event: "threading.Event | None" = None,
    ) -> None:
        with self._lock:
            _pid = (_ctx or {}).get("pid") or self.project_id
            _sid = (_ctx or {}).get("session_id") or self.session_id
            _prev = (_ctx or {}).get("prev_resp_id", self.previous_response_id)
            self._emit("turn.start", {
                "prompt": display_prompt,
                "session_id": _sid,
                "mode": self.mode,
                "project_id": _pid,
                "auto": auto,
                "project_label": project_label,
            })
            try:
                self._post_one(
                    wire_prompt,
                    url=url,
                    previous_response_id=_prev,
                    display_prompt=display_prompt,
                    auto=auto,
                    project_label=project_label,
                    prev_snap=prev_snap or {},
                    _ctx=_ctx,
                )
            except Exception as e:  # noqa: BLE001
                logger.error("[bridge] unhandled turn exception: %s", e, exc_info=True)
                self._emit("turn.error", {"error": "Something went wrong — please try again.", "auto": auto})
            finally:
                if done_event:
                    done_event.set()

    # ------------------------------------------------------------------
    # Foundry SDK client handles (hosted mode)
    # ------------------------------------------------------------------
    def _get_project_client(self) -> Any:
        """Lazily build + cache the AIProjectClient, authed as the signed-in user."""
        if self._project_client is None:
            endpoint = _resolve_project_endpoint()
            if not endpoint:
                raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT unset; cannot build SDK client")
            self._project_client = AIProjectClient(
                endpoint=endpoint,
                credential=_BridgeTokenCredential(self),
                allow_preview=True,
            )
        return self._project_client

    def _get_openai_client(self) -> Any:
        """Lazily build + cache the agent-bound OpenAI Responses client."""
        if self._openai_client is None:
            agent_name = os.environ.get("AGENT_NAME") or DEFAULT_HOSTED_AGENT_NAME
            self._openai_client = self._get_project_client().get_openai_client(agent_name=agent_name)
        return self._openai_client

    def _ensure_session_sdk(self, project_dict: dict[str, Any], isolation_key: str) -> str:
        """Return this project's server-minted session id, creating one if needed.

        The session id is minted by the platform (not chosen by us) via
        beta.agents.create_session. `isolation_key` is the stable client-owned
        key (we use the project_id) that pins one Foundry session per project;
        the returned agent_session_id is the session handle we persist and pass
        on every subsequent turn.
        """
        sid = project_dict.get("session_id")
        if sid:
            return sid
        pc = self._get_project_client()
        agent_name = os.environ.get("AGENT_NAME") or DEFAULT_HOSTED_AGENT_NAME
        kwargs: dict[str, Any] = {"agent_name": agent_name, "isolation_key": isolation_key}
        version = (os.environ.get("AGENT_VERSION") or "").strip()
        if version and VersionRefIndicator is not None:
            kwargs["version_indicator"] = VersionRefIndicator(agent_version=version)
        session = pc.beta.agents.create_session(**kwargs)
        sid = getattr(session, "agent_session_id", None) or getattr(session, "id", None)
        if not sid:
            raise RuntimeError("create_session returned no agent_session_id")
        # Persist the server-minted id IMMEDIATELY — before the first
        # responses.create — so a crash mid-turn never strands the project
        # without its session handle.
        project_dict["session_id"] = sid
        _save_projects(self._projects_data)
        self._emit("session.update", {"session_id": sid})
        logger.info("[sdk] minted session project=%s session=%s", isolation_key, sid)
        return sid

    # ------------------------------------------------------------------
    # Transport dispatch
    # ------------------------------------------------------------------
    def _post_one(
        self,
        prompt: str,
        *,
        url: str,
        previous_response_id: str | None,
        display_prompt: str = "",
        auto: bool = False,
        project_label: str = "",
        prev_snap: dict[str, str] | None = None,
        _ctx: dict | None = None,
    ) -> None:
        """Drive one turn, choosing the transport for the current mode.

        Hosted mode uses the Foundry SDK path (_post_one_sdk) — the idiomatic
        way to talk to a hosted agent: it mints a server-side session and
        streams the Responses API through the SDK's typed events. Local mode
        (a charter-agent serving /responses on localhost, which is not a
        Foundry project endpoint) and any SDK failure fall back to the retained
        raw-httpx transport (_post_one_legacy).
        """
        use_sdk = (
            self.mode == "hosted"
            and _SDK_AVAILABLE
            and os.environ.get("CHARTER_CLIENT_TRANSPORT", "sdk").strip().lower() != "legacy"
            and bool(_resolve_project_endpoint())
        )
        if use_sdk:
            try:
                self._post_one_sdk(
                    prompt,
                    url=url,
                    previous_response_id=previous_response_id,
                    display_prompt=display_prompt,
                    auto=auto,
                    project_label=project_label,
                    prev_snap=prev_snap,
                    _ctx=_ctx,
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.error("[sdk] transport failed, falling back to raw-httpx: %s", exc, exc_info=True)
        self._post_one_legacy(
            prompt,
            url=url,
            previous_response_id=previous_response_id,
            display_prompt=display_prompt,
            auto=auto,
            project_label=project_label,
            prev_snap=prev_snap,
            _ctx=_ctx,
        )

    def _post_one_sdk(
        self,
        prompt: str,
        *,
        url: str,
        previous_response_id: str | None,
        display_prompt: str = "",
        auto: bool = False,
        project_label: str = "",
        prev_snap: dict[str, str] | None = None,
        _ctx: dict | None = None,
    ) -> None:
        """Hosted-mode transport via the Foundry SDK + OpenAI Responses client.

        Mirrors the event semantics of _post_one_legacy (session-fork detection,
        empty-completion retry, consent, dashboard capture) but lets the SDK own
        the wire: create_session mints the session, responses.create(stream=True)
        yields typed events which we normalise to dicts and run through the same
        switch. The shared tail lives in _finalize_turn.
        """
        _p: dict[str, Any] = (_ctx or {}).get("project_dict") or self._current
        _pid: str = (_ctx or {}).get("pid") or self.project_id
        _skill_at_start = _p.get("skill") or "general"

        # isolation_key = stable client-owned project key; the session id is
        # whatever the platform mints for it.
        _session_id = self._ensure_session_sdk(_p, _pid)
        if _ctx is not None:
            _ctx["session_id"] = _session_id

        oc = self._get_openai_client()
        create_kwargs: dict[str, Any] = {
            "input": prompt,
            "stream": True,
            "extra_body": {"agent_session_id": _session_id},
        }
        if previous_response_id:
            create_kwargs["previous_response_id"] = previous_response_id

        response_id: str | None = None
        completed_text_parts: list[str] = []
        consent_payload: dict | None = None
        current_tool: dict[str, str] | None = None
        published_dashboard: dict | None = None

        logger.debug("[sdk] responses.create project=%s session=%s prev_resp=%s", _pid, _session_id, previous_response_id)
        event_count = 0
        stream = oc.responses.create(**create_kwargs)
        for event in stream:
            event_count += 1
            data = _sdk_event_to_dict(event)
            etype = data.get("type")
            if event_count <= 5 or event_count % 25 == 0:
                logger.debug("[sdk] evt#%d type=%s", event_count, etype)
            # Diagnostic: tool-call / function-call / item events are where the
            # publish_view dashboard payload rides. Dump them fully so we can see
            # the exact SDK event shape if the dashboard/skill/logs don't surface.
            if etype and ("function_call" in etype or "tool" in etype or "output_item" in etype):
                logger.debug("[sdk] tool-evt type=%s data=%s", etype, json.dumps(data)[:3000])

            if etype == "response.created":
                resp_obj = data.get("response") or {}
                response_id = resp_obj.get("id") or data.get("id")
                sid = resp_obj.get("agent_session_id") or data.get("agent_session_id")
                if sid:
                    _log_session(self.mode, _pid, sid)
                if sid and sid != _session_id:
                    if _session_id and previous_response_id:
                        logger.warning(
                            "[sdk] session fork detected project=%s: sent=%s got=%s — "
                            "Foundry created a new session (stale prev_resp_id).",
                            _pid, _session_id, sid,
                        )
                        self._emit("session.forked", {"old_session_id": _session_id, "new_session_id": sid})
                    _session_id = sid
                    _p["session_id"] = sid
                    _save_projects(self._projects_data)
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
                    if current_tool["name"] == "publish_view":
                        try:
                            parsed = json.loads(current_tool["args"] or "{}")
                            payload = parsed.get("payload") if isinstance(parsed, dict) else None
                            if isinstance(payload, dict) and payload.get("kind") == "dashboard":
                                published_dashboard = payload
                        except Exception:  # noqa: BLE001
                            pass
                continue

            if etype == "response.output_text.delta":
                delta = data.get("delta") or ""
                if delta:
                    self._emit("text.delta", {"delta": delta})
                continue

            if etype == "response.completed":
                resp_obj = data.get("response") or {}
                sid = resp_obj.get("agent_session_id") or data.get("agent_session_id")
                if sid:
                    _log_session(self.mode, _pid, sid)
                if sid and sid != _session_id:
                    _session_id = sid
                    _p["session_id"] = sid
                    _save_projects(self._projects_data)
                    self._emit("session.update", {"session_id": sid})
                out = resp_obj.get("output") or []
                for it in out:
                    for c in (it.get("content") or []):
                        if c.get("type") in ("output_text", "text") and c.get("text"):
                            completed_text_parts.append(c["text"])
                continue

            if etype and ("consent" in etype.lower()):
                consent_payload = data
                continue

            if etype and (etype.endswith(".failed") or etype.endswith(".error")):
                logger.error("[sdk] agent turn failed: etype=%s payload=%s", etype, json.dumps(data)[:2000])
                self._emit("turn.error", {"error": "Something went wrong — please try again."})
                return

        logger.info("[sdk] stream done: events=%d response_id=%s text_parts=%d consent=%s", event_count, response_id, len(completed_text_parts), consent_payload is not None)
        final_text = "\n".join(completed_text_parts).strip()
        self._finalize_turn(
            prompt=prompt,
            url=url,
            previous_response_id=previous_response_id,
            display_prompt=display_prompt,
            auto=auto,
            project_label=project_label,
            prev_snap=prev_snap,
            _ctx=_ctx,
            _p=_p,
            _pid=_pid,
            _session_id=_session_id,
            skill_at_start=_skill_at_start,
            response_id=response_id,
            final_text=final_text,
            consent_payload=consent_payload,
            published_dashboard=published_dashboard,
            event_count=event_count,
        )

    def _finalize_turn(
        self,
        *,
        prompt: str,
        url: str,
        previous_response_id: str | None,
        display_prompt: str,
        auto: bool,
        project_label: str,
        prev_snap: dict[str, str] | None,
        _ctx: dict | None,
        _p: dict[str, Any],
        _pid: str,
        _session_id: str | None,
        skill_at_start: str,
        response_id: str | None,
        final_text: str,
        consent_payload: dict | None,
        published_dashboard: dict | None,
        event_count: int,
    ) -> None:
        """Shared post-stream processing for the SDK transport.

        This is the byte-for-byte counterpart of _post_one_legacy's tail
        (persist prev_resp, empty-completion retry, consent retry, dashboard
        learning, turn.complete, view refresh, cache, transcript, snapshot,
        auto-diff/notify). Retries route back through the _post_one dispatcher.
        """
        if response_id:
            if _p.get("previous_response_id") != response_id:
                _p["previous_response_id"] = response_id
                _save_projects(self._projects_data)

        # Empty completion with a stale previous_response_id (the in-memory
        # transcript rolled on a container restart): retry once from a clean
        # slate. Drop ONLY previous_response_id — never agent_session_id, which
        # keys the persistent microVM + $HOME and survives restarts.
        if (
            previous_response_id
            and not consent_payload
            and not final_text
            and event_count <= 4
        ):
            logger.info("[sdk] empty completion w/ stale prev_resp; retrying without it (keeping session)")
            if _p.get("previous_response_id") is not None:
                _p["previous_response_id"] = None
                _save_projects(self._projects_data)
            self._post_one(prompt, url=url, previous_response_id=None, display_prompt=display_prompt,
                           auto=auto, project_label=project_label, prev_snap=prev_snap, _ctx=_ctx)
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
                self._post_one(prompt, url=url, previous_response_id=response_id, display_prompt=display_prompt,
                               auto=auto, project_label=project_label, prev_snap=prev_snap, _ctx=_ctx)
                return

        dashboard = published_dashboard or _maybe_extract_dashboard(final_text)
        if dashboard is not None:
            _act = dashboard.get("activity")
            _src = "publish_view" if published_dashboard is not None else "text-fence"
            logger.debug("[sdk] dashboard src=%s keys=%s activity_len=%s", _src, sorted(dashboard.keys()), len(_act) if isinstance(_act, list) else "n/a")
        if dashboard:
            mutated = False
            customer = dashboard.get("customer") or ""
            if customer and customer != _p.get("customer_name"):
                _p["customer_name"] = customer
                if (_p.get("label") or "").lower() in ("", "new project"):
                    _p["label"] = customer
                mutated = True
            skill_name = dashboard.get("skill") or ""
            if skill_name and skill_name != _p.get("skill"):
                _p["skill"] = skill_name
                mutated = True
            if mutated:
                _save_projects(self._projects_data)
                self._emit("projects.update", self._projects_payload())
        self._last_response_at = time.time()
        self._emit("turn.complete", {
            "response_id": response_id,
            "session_id": _session_id,
            "project_id": _pid,
            "text": final_text,
            "dashboard": dashboard,
            "auto": auto,
        })
        view = _project_view(_pid, mode=self.mode)
        if _pid == self.project_id and self._sync_skill_from_view(view):
            self._emit("projects.update", self._projects_payload())
            _skill_now = _p.get("skill") or "general"
            if _skill_now != skill_at_start and _skill_now != "general":
                self._emit("skill.routed", {"skill": _skill_now})
        if dashboard:
            live_dash = {k: v for k, v in dashboard.items() if k not in ("from_cache", "saved_at")}
            view["dashboard"] = live_dash
            payload_activity = dashboard.get("activity")
            if isinstance(payload_activity, list) and payload_activity:
                view["activity"] = payload_activity
        cache_dashboard = view.get("dashboard")
        if cache_dashboard:
            cache_dashboard = {k: v for k, v in cache_dashboard.items() if k not in ("from_cache", "saved_at")}
        if cache_dashboard or view.get("activity"):
            try:
                cache = _load_view_cache()
                cache[f"{self.mode}/{_pid}"] = {
                    "dashboard": cache_dashboard,
                    "activity": view.get("activity") or [],
                    "saved_at": _now_iso(),
                }
                _save_view_cache(cache)
            except Exception as ex:  # noqa: BLE001
                logger.debug("view cache write failed: %s", ex)
        if final_text and display_prompt:
            try:
                _save_transcript_turn(self.mode, _pid, display_prompt, final_text)
            except Exception as ex:  # noqa: BLE001
                logger.debug("transcript save failed: %s", ex)
        self._emit("view.update", view)

        current_dash = view.get("dashboard") or {}
        new_sections = current_dash.get("sections") or []
        if new_sections:
            self._section_snapshots[_pid] = {
                s.get("task_id", ""): s.get("status", "")
                for s in new_sections
                if s.get("task_id")
            }

        if auto:
            changes = _diff_sections(prev_snap or {}, new_sections)
            if changes:
                change_lines = "; ".join(
                    f"{c['title']}: {c['old_status']} → {c['new_status']}"
                    for c in changes
                )
                _notify(
                    f"Project Charter — {project_label}",
                    f"{len(changes)} update{'s' if len(changes) > 1 else ''}: {change_lines}",
                )
            self._emit("scheduler.done", {
                "project_id": _pid,
                "project_label": project_label,
                "changes": changes,
                "next_in_mins": _POLL_INTERVAL_MINS,
            })

    # ==================================================================
    # LEGACY raw-httpx Responses transport — retained per request.
    #
    # This is the original, proven transport that talks to the /responses
    # endpoint directly with httpx and a hand-rolled SSE parser. It is kept
    # verbatim and is NOT dead code: it is the active transport for LOCAL mode
    # (a localhost charter-agent is not a Foundry project endpoint, so the SDK
    # can't drive it) and the automatic fallback if the SDK path raises. The
    # hosted-mode default is now _post_one_sdk above. Do not delete.
    # ==================================================================
    def _post_one_legacy(
        self,
        prompt: str,
        *,
        url: str,
        previous_response_id: str | None,
        display_prompt: str = "",
        auto: bool = False,
        project_label: str = "",
        prev_snap: dict[str, str] | None = None,
        _ctx: dict | None = None,
    ) -> None:
        # NB: do NOT set store=False on the host call — the Responses host server
        # uses its own transcript store to resolve previous_response_id, and
        # opting out wipes the model's memory of prior turns (RFP grounding,
        # tool outputs, etc.). The upstream-model `store` flag is pinned in
        # runtime/foundry_host.py, which is the right place for it.
        #
        # Resolve the effective project context. _ctx is set for background checks
        # of non-active projects; it overrides self._current so writes go to the
        # correct project dict without switching the active-project pointer.
        _p: dict[str, Any] = (_ctx or {}).get("project_dict") or self._current
        _pid: str = (_ctx or {}).get("pid") or self.project_id
        _session_id: str | None = (_ctx or {}).get("session_id") or self.session_id

        _skill_at_start = _p.get("skill") or "general"
        body: dict[str, Any] = {"input": prompt, "stream": True}
        if _session_id:
            body["agent_session_id"] = _session_id
        if previous_response_id:
            body["previous_response_id"] = previous_response_id
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if _session_id:
            headers["x-agent-chat-isolation-key"] = _session_id

        response_id: str | None = None
        completed_text_parts: list[str] = []
        consent_payload: dict | None = None
        current_tool: dict[str, str] | None = None
        # Dashboard sent via the `publish_view` tool's arguments. The model is
        # instructed to call publish_view(payload=<dashboard_payload return>);
        # capturing args directly preserves every field (notably the activity
        # tail) where re-emitting the same JSON in prose tends to drop keys.
        published_dashboard: dict | None = None

        logger.debug("[bridge] POST %s project=%s session=%s prev_resp=%s", url, _pid, _session_id, previous_response_id)
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            with client.stream("POST", url, json=body, headers=headers) as resp:
                logger.debug("[bridge] <- status=%d ct=%s", resp.status_code, resp.headers.get("content-type"))
                if resp.status_code >= 400:
                    err = resp.read().decode("utf-8", errors="replace")
                    logger.error("[bridge] HTTP error: status=%d body=%s", resp.status_code, err[:2000])
                    self._emit("turn.error", {"error": "Something went wrong — please try again."})
                    return
                event_count = 0
                for evt in _iter_sse_events(resp):
                    event_count += 1
                    data = evt["data"]
                    etype_dbg = (data.get("type") if isinstance(data, dict) else None) or evt.get("event")
                    logger.debug("[bridge] evt#%d type=%s", event_count, etype_dbg)
                    if isinstance(data, dict) and isinstance(etype_dbg, str) and (
                        "function_call" in etype_dbg or "tool" in etype_dbg or "output_item" in etype_dbg
                    ):
                        logger.debug("[bridge] tool-evt type=%s data=%s", etype_dbg, json.dumps(data)[:3000])
                    if not isinstance(data, dict):
                        continue
                    etype = data.get("type") or evt["event"]

                    if etype == "response.created":
                        resp_obj = data.get("response") or {}
                        response_id = resp_obj.get("id") or data.get("id")
                        sid = resp_obj.get("agent_session_id") or data.get("agent_session_id")
                        if sid:
                            _log_session(self.mode, _pid, sid)
                        if sid and sid != _session_id:
                            if _session_id and previous_response_id:
                                # Foundry returned a DIFFERENT session_id than we
                                # sent. This means it rejected our previous_response_id
                                # and forked a new session with a blank $HOME — the
                                # original VM state is now orphaned. Warn the user
                                # rather than silently adopting the new session.
                                logger.warning(
                                    "[bridge] session fork detected project=%s: sent=%s got=%s — "
                                    "Foundry created a new session (stale prev_resp_id). "
                                    "Original $HOME state is orphaned.",
                                    _pid, _session_id, sid,
                                )
                                self._emit("session.forked", {
                                    "old_session_id": _session_id,
                                    "new_session_id": sid,
                                })
                            _session_id = sid
                            _p["session_id"] = sid
                            _save_projects(self._projects_data)
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
                            if current_tool["name"] == "publish_view":
                                try:
                                    parsed = json.loads(current_tool["args"] or "{}")
                                    payload = parsed.get("payload") if isinstance(parsed, dict) else None
                                    logger.debug(
                                        "[bridge] publish_view parsed: top_keys=%s payload_kind=%s",
                                        sorted(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__,
                                        payload.get("kind") if isinstance(payload, dict) else "n/a",
                                    )
                                    if isinstance(payload, dict) and payload.get("kind") == "dashboard":
                                        published_dashboard = payload
                                except Exception as _ex:  # noqa: BLE001
                                    logger.debug("[bridge] publish_view parse failed: %s args=%s", _ex, (current_tool["args"] or "")[:1500])
                        continue

                    if etype == "response.output_text.delta":
                        delta = data.get("delta") or ""
                        if delta:
                            self._emit("text.delta", {"delta": delta})
                        continue

                    if etype == "response.completed":
                        resp_obj = data.get("response") or {}
                        sid = resp_obj.get("agent_session_id") or data.get("agent_session_id")
                        if sid:
                            _log_session(self.mode, _pid, sid)
                        if sid and sid != _session_id:
                            if _session_id and previous_response_id:
                                logger.warning(
                                    "[bridge] session fork on completed project=%s: sent=%s got=%s",
                                    _pid, _session_id, sid,
                                )
                            _session_id = sid
                            _p["session_id"] = sid
                            _save_projects(self._projects_data)
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
                        logger.error("[bridge] agent turn failed: etype=%s payload=%s", etype, json.dumps(data)[:2000])
                        self._emit("turn.error", {"error": "Something went wrong — please try again."})
                        return

        logger.info("[bridge] stream done: events=%d response_id=%s text_parts=%d consent=%s", event_count, response_id, len(completed_text_parts), consent_payload is not None)
        final_text = "\n".join(completed_text_parts).strip()
        if response_id:
            if _p.get("previous_response_id") != response_id:
                _p["previous_response_id"] = response_id
                _save_projects(self._projects_data)

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
            logger.info("[bridge] empty completion w/ stale prev_resp; retrying without it (keeping session)")
            if _p.get("previous_response_id") is not None:
                _p["previous_response_id"] = None
                _save_projects(self._projects_data)
            self._post_one(prompt, url=url, previous_response_id=None, display_prompt=display_prompt,
                           auto=auto, project_label=project_label, prev_snap=prev_snap, _ctx=_ctx)
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
                self._post_one(prompt, url=url, previous_response_id=response_id, display_prompt=display_prompt,
                               auto=auto, project_label=project_label, prev_snap=prev_snap, _ctx=_ctx)
                return

        dashboard = published_dashboard or _maybe_extract_dashboard(final_text)
        if dashboard is not None:
            _act = dashboard.get("activity")
            _src = "publish_view" if published_dashboard is not None else "text-fence"
            logger.debug("[bridge] dashboard src=%s keys=%s activity_len=%s", _src, sorted(dashboard.keys()), len(_act) if isinstance(_act, list) else "n/a")
        # Learn the customer name + skill from the dashboard so the sidebar reflects
        # what the agent actually decided. The skill name is the agent's declaration
        # (written into project_log.json by the chosen skill's kickoff tool); we never
        # default it client-side.
        if dashboard:
            mutated = False
            customer = dashboard.get("customer") or ""
            if customer and customer != _p.get("customer_name"):
                _p["customer_name"] = customer
                if (_p.get("label") or "").lower() in ("", "new project"):
                    _p["label"] = customer
                mutated = True
            skill_name = dashboard.get("skill") or ""
            if skill_name and skill_name != _p.get("skill"):
                _p["skill"] = skill_name
                mutated = True
            if mutated:
                _save_projects(self._projects_data)
                self._emit("projects.update", self._projects_payload())
        self._last_response_at = time.time()
        self._emit("turn.complete", {
            "response_id": response_id,
            "session_id": _session_id,
            "project_id": _pid,
            "text": final_text,
            "dashboard": dashboard,
            "auto": auto,
        })
        # Refresh the disk-derived view (activity audit + canonical dashboard
        # from project_log.json) so the UI matches the agent's source of truth.
        view = _project_view(_pid, mode=self.mode)
        # _sync_skill_from_view writes to self._current; only call it for the
        # active project to avoid corrupting the active project's skill record
        # during a background check of a non-active project.
        if _pid == self.project_id and self._sync_skill_from_view(view):
            self._emit("projects.update", self._projects_payload())
            _skill_now = _p.get("skill") or "general"
            if _skill_now != _skill_at_start and _skill_now != "general":
                self._emit("skill.routed", {"skill": _skill_now})
        # If this turn produced a fresh dashboard, treat it as live state —
        # overrides whatever stale cache `_project_view` may have returned for
        # hosted mode, and strips any from_cache/saved_at markers.
        if dashboard:
            live_dash = {k: v for k, v in dashboard.items() if k not in ("from_cache", "saved_at")}
            view["dashboard"] = live_dash
            # The dashboard payload carries the project's activity tail
            # (`dashboard.activity`) so hosted mode — which can't read the
            # microVM's $HOME — still has a real audit stream to render.
            # Prefer it over the local-disk read for the in-flight view.
            payload_activity = dashboard.get("activity")
            if isinstance(payload_activity, list) and payload_activity:
                view["activity"] = payload_activity
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
                cache[f"{self.mode}/{_pid}"] = {
                    "dashboard": cache_dashboard,
                    "activity": view.get("activity") or [],
                    "saved_at": _now_iso(),
                }
                _save_view_cache(cache)
            except Exception as ex:  # noqa: BLE001
                logger.debug("view cache write failed: %s", ex)
        # Persist the conversation turn so it can be re-rendered when the user
        # switches away and returns to this project.
        if final_text and display_prompt:
            try:
                _save_transcript_turn(self.mode, _pid, display_prompt, final_text)
            except Exception as ex:  # noqa: BLE001
                logger.debug("transcript save failed: %s", ex)
        # view.update: always emit; the UI filters by project_id so non-active
        # project views don't overwrite the dashboard the user is looking at.
        self._emit("view.update", view)

        # Always update the section snapshot so the next auto-check can diff accurately.
        current_dash = view.get("dashboard") or {}
        new_sections = current_dash.get("sections") or []
        if new_sections:
            self._section_snapshots[_pid] = {
                s.get("task_id", ""): s.get("status", "")
                for s in new_sections
                if s.get("task_id")
            }

        # If this was a background auto-check, diff and notify on changes.
        if auto:
            changes = _diff_sections(prev_snap or {}, new_sections)
            if changes:
                change_lines = "; ".join(
                    f"{c['title']}: {c['old_status']} → {c['new_status']}"
                    for c in changes
                )
                _notify(
                    f"Project Charter — {project_label}",
                    f"{len(changes)} update{'s' if len(changes) > 1 else ''}: {change_lines}",
                )
            self._emit("scheduler.done", {
                "project_id": _pid,
                "project_label": project_label,
                "changes": changes,
                "next_in_mins": _POLL_INTERVAL_MINS,
            })
