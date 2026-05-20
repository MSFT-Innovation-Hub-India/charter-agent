"""Generic `$HOME` I/O for the per-session microVM.

Path-based. No knowledge of any project shape. Skills decide what files exist
and what's inside them. Two guarantees this module commits to:

1. **Atomic writes** — every write goes to `<path>.tmp` then `os.replace`s into
   place, so a crash or eviction cannot leave a half-written file.
2. **Path containment** — every relative `path` argument is resolved under
   `$HOME` and rejected if it escapes (no `..`, no absolute paths). This is
   what makes it safe to expose state I/O as agent-side tools the LLM drives.

The agent-side tool wrappers that surface these functions to the model live in
`runtime/state_tools.py`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def home_dir() -> Path:
    return Path(os.environ.get("HOME", os.path.expanduser("~")))


def _resolve(rel_path: str) -> Path:
    if not rel_path or rel_path.strip() != rel_path:
        raise ValueError("state: path must be a non-empty, non-whitespace string.")
    if os.path.isabs(rel_path):
        raise ValueError(f"state: path {rel_path!r} must be relative.")
    candidate = (home_dir() / rel_path).resolve()
    root = home_dir().resolve()
    try:
        candidate.relative_to(root)
    except ValueError as e:
        raise ValueError(f"state: path {rel_path!r} escapes $HOME.") from e
    return candidate


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, target)


# --- text -----------------------------------------------------------------


def read_text(rel_path: str) -> str:
    p = _resolve(rel_path)
    if not p.exists():
        raise FileNotFoundError(rel_path)
    return p.read_text(encoding="utf-8")


def write_text(rel_path: str, content: str) -> Path:
    p = _resolve(rel_path)
    _atomic_write_bytes(p, content.encode("utf-8"))
    return p


# --- json -----------------------------------------------------------------


def read_json(rel_path: str) -> Any:
    return json.loads(read_text(rel_path))


def write_json(rel_path: str, obj: Any) -> Path:
    payload = json.dumps(obj, indent=2, sort_keys=False, ensure_ascii=False).encode("utf-8")
    p = _resolve(rel_path)
    _atomic_write_bytes(p, payload)
    return p


# --- ndjson (append-only) -------------------------------------------------


def append_ndjson(rel_path: str, entry: Any) -> Path:
    p = _resolve(rel_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return p


# --- listing / existence --------------------------------------------------


def exists(rel_path: str) -> bool:
    try:
        return _resolve(rel_path).exists()
    except ValueError:
        return False


def list_files(rel_path: str = ".") -> list[str]:
    base = _resolve(rel_path)
    if not base.exists():
        return []
    if not base.is_dir():
        return [str(base.relative_to(home_dir().resolve()).as_posix())]
    root = home_dir().resolve()
    return sorted(
        str(p.resolve().relative_to(root).as_posix())
        for p in base.rglob("*")
        if p.is_file()
    )


# --- echo-demo counter (Phase 1 smoke verb) -------------------------------

_COUNTER_FILE = "state.json"


def bump_counter() -> int:
    s: dict[str, Any] = read_json(_COUNTER_FILE) if exists(_COUNTER_FILE) else {}
    n = int(s.get("counter", 0)) + 1
    s["counter"] = n
    write_json(_COUNTER_FILE, s)
    return n
