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

Local-mode safety: in the Foundry hosted container, `$HOME` is set by the
platform to a per-session sandbox directory. In local dev that env var is
absent, and falling back to `~` would let the agent walk the developer's real
Windows/Linux home. Instead we pin `$HOME` to `<repo>/.charter-agent-home/`
on first access and rebind `os.environ['HOME']` so every downstream resolver
agrees. State persists across local agent restarts, which is what you want
for multi-turn dev.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Hard caps for tool outputs surfaced to the model. The OpenAI Responses API
# rejects a single tool result string over 10 MB, and even well below that a
# multi-MB listing wastes context and inference time. These bounds keep
# `state_list_files` honest regardless of where `$HOME` ends up pointing.
_LIST_MAX_ENTRIES = 500

_LOCAL_HOME_PINNED = False


def _pin_local_home() -> None:
    """Bind `$HOME` to a project-scoped scratch dir when running outside the
    Foundry sandbox container. Idempotent.
    """
    global _LOCAL_HOME_PINNED
    if _LOCAL_HOME_PINNED or os.environ.get("HOME"):
        return
    # `state.py` lives at agent/src/charter_agent/state.py; agent root is 3 up.
    repo_local_home = (Path(__file__).resolve().parents[2] / ".charter-agent-home").resolve()
    repo_local_home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(repo_local_home)
    _LOCAL_HOME_PINNED = True
    _LOGGER.warning(
        "state: HOME not set by platform; pinned local sandbox to %s", repo_local_home
    )


def home_dir() -> Path:
    _pin_local_home()
    return Path(os.environ["HOME"])


# --- active project ------------------------------------------------------
#
# `$HOME/.active_project` is a single-line text file naming the current
# project (e.g. "p-9a3f12"). Project files live under `$HOME/projects/<id>/`.
# When unset, the agent defaults to "default" so cold-start dev still works,
# but the desktop client provides a context preamble on every turn that the
# skill uses to call `set_active_project` before any other tool.

_ACTIVE_FILE = ".active_project"
_DEFAULT_PROJECT = "default"
_PROJECT_ID_OK = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


def _validate_project_id(pid: str) -> str:
    if not pid or not pid.strip():
        raise ValueError("state: project_id must be a non-empty string.")
    pid = pid.strip()
    if len(pid) > 64:
        raise ValueError("state: project_id must be <= 64 chars.")
    if any(ch not in _PROJECT_ID_OK for ch in pid):
        raise ValueError(f"state: project_id {pid!r} has invalid characters.")
    return pid


def active_project_id() -> str:
    p = home_dir() / _ACTIVE_FILE
    if not p.exists():
        return _DEFAULT_PROJECT
    val = p.read_text(encoding="utf-8").strip() or _DEFAULT_PROJECT
    try:
        return _validate_project_id(val)
    except ValueError:
        return _DEFAULT_PROJECT


def set_active_project_id(pid: str) -> str:
    pid = _validate_project_id(pid)
    target = home_dir() / _ACTIVE_FILE
    _atomic_write_bytes(target, pid.encode("utf-8"))
    (home_dir() / "projects" / pid).mkdir(parents=True, exist_ok=True)
    return pid


def project_path(filename: str) -> str:
    """Return a `$HOME`-relative path scoped to the active project.

    Use this for every file that belongs to a specific project
    (charter, log, deliverables, drafts). Cross-project files (e.g. the
    `.active_project` pointer itself) stay at `$HOME` root.
    """
    pid = active_project_id()
    return f"projects/{pid}/{filename}"


def list_project_ids() -> list[str]:
    root = home_dir() / "projects"
    if not root.exists():
        return []
    out: list[str] = []
    for child in sorted(root.iterdir()):
        if child.is_dir():
            try:
                out.append(_validate_project_id(child.name))
            except ValueError:
                continue
    return out


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


def patch_json(rel_path: str, patch: dict[str, Any]) -> Path:
    """Merge top-level keys from `patch` into an existing JSON file.

    Reads the current file (or starts from `{}` if it doesn't exist), applies
    `patch` as a shallow merge at the top level, then atomically rewrites.
    Nested objects are replaced, not recursively merged — callers that need
    deep merging should read, modify, and write the nested object themselves.
    """
    try:
        existing: dict[str, Any] = read_json(rel_path)
        if not isinstance(existing, dict):
            existing = {}
    except FileNotFoundError:
        existing = {}
    existing.update(patch)
    return write_json(rel_path, existing)


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


def list_files(rel_path: str = ".", recursive: bool = False) -> list[str]:
    base = _resolve(rel_path)
    if not base.exists():
        return []
    if not base.is_dir():
        return [str(base.relative_to(home_dir().resolve()).as_posix())]
    root = home_dir().resolve()
    iterator = base.rglob("*") if recursive else base.glob("*")
    out: list[str] = []
    truncated = False
    for p in iterator:
        if not p.is_file():
            continue
        if len(out) >= _LIST_MAX_ENTRIES:
            truncated = True
            break
        out.append(str(p.resolve().relative_to(root).as_posix()))
    out.sort()
    if truncated:
        out.append(f"…<truncated at {_LIST_MAX_ENTRIES} entries; pass a narrower path or recursive=False>")
    return out
