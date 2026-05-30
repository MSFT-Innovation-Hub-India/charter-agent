"""Native Windows toast notifications."""

from __future__ import annotations

from .config import ASSETS_DIR, logger


def _notify(title: str, msg: str) -> None:
    """Fire a native Windows toast. No-ops if winotify is not installed."""
    try:
        from winotify import Notification  # type: ignore
        _icon = ASSETS_DIR / "app_icon.png"
        icon = str(_icon) if _icon.exists() else ""
        toast = Notification(app_id="Project Charter", title=title, msg=msg[:300], icon=icon)
        toast.show()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[notify] OS toast failed: %s", exc)
