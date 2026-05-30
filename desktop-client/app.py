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

Implementation note: the client is split into the ``charter_client`` package
(config, protocol, auth, storage, notifications, poller, bridge, tray). This
module is the thin entry point that wires the window, the bridge, the poller,
and the tray together.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time

import webview

from charter_client.bridge import Bridge
from charter_client.config import (
    DEFAULT_LOCAL,
    APP_ICON_ICO,
    APP_ICON_PNG,
    UI_HTML,
    WINDOW_TITLE,
    _load_agent_env,
    _resolve_hosted_url,
    logger,
)
from charter_client.notifications import _notify
from charter_client.poller import AutoPoller
from charter_client.tray import (
    _acquire_single_instance_lock,
    _quit_requested,
    _set_taskbar_icon,
    _setup_tray,
)


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

    if not _acquire_single_instance_lock():
        logger.warning("Another instance of Project Charter is already running.")
        _notify("Project Charter", "Already running — look for the tray icon.")
        return 0

    if not args.hosted_url:
        args.hosted_url = _resolve_hosted_url()

    if args.mode == "hosted" and not args.hosted_url:
        logger.warning("AGENT_ENDPOINT_HOSTED not set; starting in 'local' mode.")
        args.mode = "local"

    if not UI_HTML.exists():
        logger.error("missing ui.html at %s", UI_HTML)
        return 1

    bridge = Bridge(local_url=args.local_url, hosted_url=args.hosted_url, initial_mode=args.mode)

    poller = AutoPoller(bridge)
    poller.start()

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

    def _on_closing() -> bool | None:
        """Hide to tray instead of destroying the window."""
        if _quit_requested.is_set():
            return None   # allow the real close
        try:
            window.hide()
            window._tray_hidden = True   # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        return False  # cancel the close event

    window.events.closing += _on_closing
    if sys.platform == "win32":
        _setup_tray(window)

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
                logger.debug("[startup] hwnd=0x%x title=%r", hwnd, WINDOW_TITLE)
                _set_taskbar_icon(int(hwnd))
            except Exception as ex:  # noqa: BLE001
                logger.warning("[startup] hook failed: %s", ex)

        threading.Thread(target=_worker, name="taskbar-icon", daemon=True).start()

    webview.start(_on_started, gui="edgechromium", debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
