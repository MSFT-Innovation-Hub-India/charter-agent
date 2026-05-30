"""Background auto-poll scheduler.

Triggers an autonomous "check and continue" turn for eligible projects on a
configurable interval. Skill-agnostic — the agent decides what to do based on
the skill declared in the preamble.
"""

from __future__ import annotations

import threading
import time

from .config import (
    _POLL_BIZ_HOURS_ONLY,
    _POLL_INTERVAL_MINS,
    _skill_background_sync,
    logger,
)
from .notifications import _notify


class AutoPoller:
    """Background scheduler that autonomously triggers a 'check and continue'
    turn for the active project on a configurable interval.

    Skill-agnostic: the prompt is generic and the agent decides what to do
    based on the skill declared in the preamble. Works for any skill, not just
    sow-response.

    Configurable via env vars:
      CHARTER_POLL_INTERVAL_MINS   — interval in minutes (default 30)
      CHARTER_POLL_BIZ_HOURS_ONLY  — "1" restricts to Mon-Fri 07:00-20:00 local
    """

    def __init__(self, bridge: "Bridge") -> None:  # noqa: F821
        self._bridge = bridge
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="auto-poller", daemon=True)
        self._thread.start()
        logger.info("[poller] started interval=%dmin biz_hours_only=%s",
                    _POLL_INTERVAL_MINS, _POLL_BIZ_HOURS_ONLY)

    def stop(self) -> None:
        self._stop.set()

    def _in_business_hours(self) -> bool:
        if not _POLL_BIZ_HOURS_ONLY:
            return True
        import datetime
        now = datetime.datetime.now()
        if now.weekday() >= 5:  # Saturday / Sunday
            return False
        return 7 <= now.hour < 20

    def _loop(self) -> None:
        interval_secs = _POLL_INTERVAL_MINS * 60
        # Initial delay: one full interval before the first auto-check so the
        # user has time to interact on launch.
        self._stop.wait(interval_secs)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                logger.exception("[poller] tick error")
            self._stop.wait(interval_secs)

    def _tick(self) -> None:
        if not self._in_business_hours():
            logger.info("[poller] outside business hours, skipping")
            return

        bridge = self._bridge

        # Require a valid token.
        if not bridge.token or time.time() >= bridge.token_expires_at - 60:
            logger.info("[poller] token missing/expired, skipping tick")
            return

        # Skip if another turn is already running (non-blocking lock probe).
        if not bridge._lock.acquire(blocking=False):
            logger.info("[poller] bridge busy, deferring tick")
            return
        bridge._lock.release()

        # Collect all projects in the current mode that have a live session and
        # declare background_sync: true in their skill's SKILL.md.
        mode_projects = bridge._projects_data.get("projects", {}).get(bridge.mode, {})
        active_pid = bridge.project_id
        eligible: list[tuple[str, dict]] = [
            (pid, proj)
            for pid, proj in mode_projects.items()
            if proj.get("session_id") and _skill_background_sync(proj.get("skill") or "general")
        ]
        if not eligible:
            logger.info("[poller] no eligible projects for mode=%r", bridge.mode)
            return

        # Active project first — user sees their current view update immediately.
        eligible.sort(key=lambda x: (x[0] != active_pid))

        for pid, project in eligible:
            if self._stop.is_set():
                break
            project_label = project.get("customer_name") or project.get("label") or pid
            logger.info("[poller] tick project=%s label=%r", pid, project_label)

            bridge._emit("scheduler.tick", {
                "project_id": pid,
                "project_label": project_label,
                "next_in_mins": _POLL_INTERVAL_MINS,
            })
            _notify("Project Charter", f"Checking {project_label}…")

            # Build an explicit project context so _post_one writes session updates
            # to the correct project dict even when pid != the active project.
            _ctx = {
                "pid": pid,
                "session_id": project.get("session_id"),
                "project_dict": bridge._mode_projects()[pid],
            }
            done = threading.Event()
            bridge._run_auto_check(pid, project_label, _ctx=_ctx, done_event=done)
            if not done.wait(timeout=300):
                logger.warning("[poller] auto-check timed out for project %s", pid)

            if self._stop.is_set():
                break
            if len(eligible) > 1:
                time.sleep(3)  # brief gap between sequential project checks
