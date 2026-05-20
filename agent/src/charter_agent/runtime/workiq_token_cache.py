"""MSAL token-cache persistence helper.

Thin shim over ``state.py`` so the cache lives in ``$HOME`` (the per-session
microVM root) and is written atomically. Kept separate so tests can mock the
filesystem boundary without touching MSAL.
"""

from __future__ import annotations

from .. import state


def load(rel_path: str) -> str | None:
    if not state.exists(rel_path):
        return None
    try:
        return state.read_text(rel_path)
    except (FileNotFoundError, OSError):
        return None


def save(rel_path: str, serialised: str) -> None:
    state.write_text(rel_path, serialised)
