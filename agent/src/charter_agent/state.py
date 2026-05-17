"""$HOME read/write helpers. Atomic writes (temp + rename)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def home_dir() -> Path:
    return Path(os.environ.get("HOME", os.path.expanduser("~")))


def state_path() -> Path:
    return home_dir() / "state.json"


def read_state() -> dict[str, Any]:
    p = state_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def write_state(state: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def bump_counter() -> int:
    state = read_state()
    n = int(state.get("counter", 0)) + 1
    state["counter"] = n
    write_state(state)
    return n
