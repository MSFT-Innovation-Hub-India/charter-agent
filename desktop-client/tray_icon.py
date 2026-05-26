"""Win32 system-tray icon for Project Charter Desktop Agent.

Uses raw ctypes / Win32 — no third-party tray library required.
Runs its own daemon message-pump thread so the pywebview event loop
is never blocked.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------
WM_DESTROY   = 0x0002
WM_COMMAND   = 0x0111
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_QUIT      = 0x0012
WM_USER      = 0x0400
WM_TRAYICON  = WM_USER + 20   # custom Shell_NotifyIcon callback message

NIM_ADD     = 0
NIM_DELETE  = 2
NIF_MESSAGE = 0x0001
NIF_ICON    = 0x0002
NIF_TIP     = 0x0004

IMAGE_ICON    = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE  = 0x0040

MF_STRING    = 0x0000
MF_SEPARATOR = 0x0800
TPM_LEFTALIGN   = 0x0000
TPM_BOTTOMALIGN = 0x0020

IDM_SHOW = 1001
IDM_QUIT = 1002

# ---------------------------------------------------------------------------
# ctypes types — WNDPROC ref must outlive the thread to prevent GC
# ---------------------------------------------------------------------------
WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,   # LRESULT
    ctypes.c_void_p,    # HWND
    ctypes.c_uint,      # UINT msg
    ctypes.c_size_t,    # WPARAM
    ctypes.c_ssize_t,   # LPARAM
)


# ---------------------------------------------------------------------------
# Win32 structures
# ---------------------------------------------------------------------------

class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style",         wt.UINT),
        ("lpfnWndProc",   WNDPROC),
        ("cbClsExtra",    ctypes.c_int),
        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",     ctypes.c_void_p),
        ("hIcon",         ctypes.c_void_p),
        ("hCursor",       ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName",  wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize",           wt.DWORD),
        ("hWnd",             ctypes.c_void_p),
        ("uID",              wt.UINT),
        ("uFlags",           wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon",            ctypes.c_void_p),
        ("szTip",            ctypes.c_wchar * 128),
        ("dwState",          wt.DWORD),
        ("dwStateMask",      wt.DWORD),
        ("szInfo",           ctypes.c_wchar * 256),
        ("uVersion",         wt.UINT),
        ("szInfoTitle",      ctypes.c_wchar * 64),
        ("dwInfoFlags",      wt.DWORD),
    ]


# ---------------------------------------------------------------------------

class TrayIcon:
    """System-tray icon that runs a Win32 message pump in its own daemon thread."""

    def __init__(
        self,
        *,
        on_show: Callable[[], None],
        on_quit: Callable[[], None],
        icon_path: str | None = None,
        tooltip: str = "Project Charter",
    ) -> None:
        self._on_show   = on_show
        self._on_quit   = on_quit
        self._icon_path = icon_path
        self._tooltip   = tooltip
        self._hwnd: int = 0
        self._thread: threading.Thread | None = None
        self._wndproc_ref = WNDPROC(self._wndproc)   # must outlive the thread

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="tray-icon"
        )
        self._thread.start()

    def stop(self) -> None:
        if self._hwnd:
            try:
                ctypes.windll.user32.PostMessageW(
                    ctypes.c_void_p(self._hwnd), WM_QUIT, 0, 0
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal

    def _run(self) -> None:
        try:
            self._run_inner()
        except Exception as exc:
            logger.warning("[tray] crashed: %s", exc)

    def _run_inner(self) -> None:
        user32  = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32  = ctypes.windll.shell32

        # 64-bit-safe return types so HANDLE values aren't truncated.
        user32.CreateWindowExW.restype = ctypes.c_void_p
        user32.LoadImageW.restype      = ctypes.c_void_p
        user32.LoadIconW.restype       = ctypes.c_void_p
        user32.CreatePopupMenu.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]

        hinstance  = kernel32.GetModuleHandleW(None)
        class_name = "CharterAgentTrayClass_v1"

        wc = WNDCLASSW()
        wc.lpfnWndProc   = self._wndproc_ref
        wc.hInstance     = hinstance
        wc.lpszClassName = class_name
        user32.RegisterClassW(ctypes.byref(wc))

        self._hwnd = user32.CreateWindowExW(
            0, class_name, "Charter Agent Tray",
            0, 0, 0, 0, 0,
            None, None, hinstance, None,
        ) or 0
        if not self._hwnd:
            logger.warning("[tray] CreateWindowExW failed — tray unavailable")
            return

        # Load icon from file; fall back to Windows default application icon.
        hicon: int = 0
        if self._icon_path:
            hicon = user32.LoadImageW(
                None, self._icon_path, IMAGE_ICON,
                0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE,
            ) or 0
        if not hicon:
            hicon = user32.LoadIconW(None, 32512) or 0   # IDI_APPLICATION

        nid = NOTIFYICONDATAW()
        nid.cbSize          = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd            = self._hwnd
        nid.uID             = 1
        nid.uFlags          = NIF_ICON | NIF_MESSAGE | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon           = hicon
        nid.szTip           = self._tooltip[:127]
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        logger.info("[tray] icon registered in system tray")

        # Message pump — blocks until WM_QUIT is posted (via stop() or quit).
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        logger.info("[tray] icon removed from system tray")

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        user32 = ctypes.windll.user32
        if msg == WM_TRAYICON:
            if lparam == WM_LBUTTONUP:
                self._on_show()
            elif lparam == WM_RBUTTONUP:
                self._show_menu(hwnd)
            return 0
        if msg == WM_COMMAND:
            cmd = wparam & 0xFFFF
            if cmd == IDM_SHOW:
                self._on_show()
            elif cmd == IDM_QUIT:
                self._on_quit()
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(
            ctypes.c_void_p(hwnd), msg,
            ctypes.c_size_t(wparam), ctypes.c_ssize_t(lparam),
        )

    def _show_menu(self, hwnd: int) -> None:
        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING,    IDM_SHOW, "Show / Hide")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0,        None)
        user32.AppendMenuW(menu, MF_STRING,    IDM_QUIT, "Quit")
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.TrackPopupMenu(
            menu, TPM_LEFTALIGN | TPM_BOTTOMALIGN,
            pt.x, pt.y, 0, ctypes.c_void_p(hwnd), None,
        )
        user32.DestroyMenu(menu)
