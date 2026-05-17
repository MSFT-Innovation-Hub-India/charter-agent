"""Load agentskills.io-conformant skills from `agent/skills/*/SKILL.md`.

Phase 1: load + validate frontmatter; no skills shipped yet, so this returns
an empty list at boot. The full registration into the `ChatAgent` lands in
Phase 2 once the first skill exists.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path
    allowed_tools: list[str]


def _parse(skill_md: Path) -> Skill:
    text = skill_md.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"{skill_md}: missing YAML frontmatter")
    meta = yaml.safe_load(m.group(1)) or {}
    body = m.group(2).strip()

    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(f"{skill_md}: invalid `name` (must be kebab-case, 1–64 chars)")
    if name != skill_md.parent.name:
        raise ValueError(f"{skill_md}: `name` ({name!r}) must equal parent dir name")
    if not isinstance(description, str) or not (1 <= len(description) <= 1024):
        raise ValueError(f"{skill_md}: `description` required, 1–1024 chars")

    allowed = meta.get("allowed-tools") or []
    if isinstance(allowed, str):
        allowed = allowed.split()

    return Skill(
        name=name,
        description=description,
        body=body,
        path=skill_md,
        allowed_tools=list(allowed),
    )


def skills_dir() -> Path:
    override = os.environ.get("CHARTER_AGENT_SKILLS_DIR")
    if override:
        return Path(override)
    # runtime/skill_loader.py → runtime → charter_agent → src → agent; agent/skills/ holds the skill folders.
    return Path(__file__).resolve().parents[3] / "skills"


_cache: dict[str, Skill] = {}


def load_all() -> list[Skill]:
    root = skills_dir()
    _cache.clear()
    if not root.exists():
        return []
    skills: list[Skill] = []
    for skill_md in sorted(root.glob("*/SKILL.md")):
        skill = _parse(skill_md)
        _cache[skill.name] = skill
        skills.append(skill)
    return skills


def get(name: str) -> Skill:
    if not _cache:
        # Lazy load on first request (covers test-time and any path that skipped boot).
        load_all()
    try:
        return _cache[name]
    except KeyError as e:
        raise KeyError(f"skill_loader: no skill named {name!r} (have {list(_cache)})") from e
