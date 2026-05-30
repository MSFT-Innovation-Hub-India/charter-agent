"""Single-instance lock and Windows system-tray helpers."""

from __future__ import annotations

import ctypes
import sys
import threading

import webview

from .config import APP_ICON_ICO, WINDOW_TITLE, logger

_SINGLE_INSTANCE_HANDLE: "ctypes.c_void_p | None" = None
_quit_requested = threading.Event()
_tray: object | None = None


def _acquire_single_instance_lock() -> bool:
    """Return False if another instance is already running (Windows only)."""
    global _SINGLE_INSTANCE_HANDLE
    if sys.platform != "win32":
        return True
    try:
        ERROR_ALREADY_EXISTS = 183
        handle = ctypes.windll.kernel32.CreateMutexW(
            None, True, "Local\\CharterAgent-SingleInstance-v1"
        )
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            ctypes.windll.kernel32.CloseHandle(handle)
            return False
        _SINGLE_INSTANCE_HANDLE = handle   # keep alive for process lifetime
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[startup] single-instance lock failed: %s", exc)
        return True  # allow startup on error


def _setup_tray(window: webview.Window) -> None:
    """Start the Win32 system-tray icon (Windows only). No-op on other platforms."""
    global _tray
    if sys.platform != "win32":
        return
    try:
        from tray_icon import TrayIcon  # type: ignore[import]
    except ImportError:
        logger.info("[tray] tray_icon module not found — skipping")
        return

    def _toggle() -> None:
        try:
            if getattr(window, "_tray_hidden", False):
                window.show()
                window._tray_hidden = False  # type: ignore[attr-defined]
            else:
                window.hide()
                window._tray_hidden = True   # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[tray] toggle: %s", exc)

    def _quit() -> None:
        _quit_requested.set()
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass
        import os as _os
        _os._exit(0)

    try:
        icon_path = str(APP_ICON_ICO) if APP_ICON_ICO.exists() else None
        _tray = TrayIcon(
            on_show=_toggle,
            on_quit=_quit,
            icon_path=icon_path,
            tooltip="Project Charter",
        )
        _tray.start()  # type: ignore[attr-defined]
        logger.info("[tray] system tray icon started")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[tray] setup failed: %s", exc)


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
        logger.info("icon: hwnd=0x%x big=%s small=%s file=%s", hwnd, hicon_big, hicon_small, ico)
        if hicon_big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
        if hicon_small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
    except Exception as ex:  # noqa: BLE001
        logger.warning("taskbar icon set failed: %s", ex)
