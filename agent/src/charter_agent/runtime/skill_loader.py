"""Load agentskills.io-conformant skills from `agent/skills/**/SKILL.md`.

Skills may live at any depth under `agent/skills/`. Top-level skills
(e.g. `general/SKILL.md`) and phase skills nested under a parent skill
(e.g. `sow-response/rfp-search/SKILL.md`) are treated identically — the
`name:` frontmatter field is the registered key, not the directory name.

Each skill is parsed into a `Skill` manifest. A `SkillBundle` pairs the
manifest with the concrete tool callables it is allowed to use, resolved
from two sources:

  1. **Shared in-process tools** — generic `@tool`-decorated functions
     registered via `register_tools(...)` at boot (today: the seven
     `STATE_TOOLS` from `runtime.state_tools`).
  2. **Per-skill domain tools** — auto-discovered by importing
     `charter_agent.skills.<snake_name>.tools` and reading its `TOOLS`
     attribute. Missing module is fine (skill has no domain tools).

Strict allowed-tools policy:
  - Every name in `allowed-tools` is classified as either "in-process"
    (matches a `@tool` function we know about) or "external" (anything
    else — presumed to live in the Foundry Toolbox).
  - In-process names MUST resolve to a known tool, otherwise boot fails.
  - External names are passed through to the warm Agent's MCP attachment
    as a literal allowlist. Glob entries (containing `*`) are left to MCP
    runtime semantics — MAF's `MCPStreamableHTTPTool.allowed_tools` takes
    literals, so when globs are present we leave the toolbox surface
    unrestricted and rely on the skill body to discipline tool choice.
  - An empty `allowed-tools` is treated as "no in-process restriction,
    toolbox unrestricted" (back-compat); skills SHOULD declare explicitly.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    """Parsed SKILL.md manifest."""

    name: str
    description: str
    body: str
    path: Path
    allowed_tools: list[str]


@dataclass(frozen=True)
class SkillBundle:
    """A skill manifest plus the resolved tools it is allowed to call.

    `inprocess_tools` are concrete `@tool` callables to attach to the warm
    Agent. `toolbox_allowlist` is the literal set of MCP tool names the
    Agent's `MCPStreamableHTTPTool` should restrict to, or `None` meaning
    "no restriction" (used when the manifest contains globs or no external
    names at all).
    """

    manifest: Skill
    inprocess_tools: tuple[Any, ...]
    toolbox_allowlist: tuple[str, ...] | None = field(default=None)


_TOOL_REGISTRY: dict[str, Any] = {}

_skill_cache: dict[str, Skill] = {}
_bundle_cache: dict[str, SkillBundle] = {}


def _tool_name(fn: Any) -> str:
    return str(getattr(fn, "name", None) or getattr(fn, "__name__", repr(fn)))


def register_tools(tools: list[Any]) -> None:
    """Add shared in-process tools to the registry. Idempotent on name."""
    for fn in tools:
        _TOOL_REGISTRY[_tool_name(fn)] = fn


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


def _load_skill_domain_tools(skill_name: str) -> list[Any]:
    """Import `charter_agent.skills.<snake>.tools` and return its `TOOLS`."""
    module_path = f"charter_agent.skills.{skill_name.replace('-', '_')}.tools"
    try:
        mod = importlib.import_module(module_path)
    except ModuleNotFoundError:
        return []
    tools = getattr(mod, "TOOLS", None)
    if tools is None:
        return []
    if not isinstance(tools, list):
        raise TypeError(f"{module_path}.TOOLS must be a list, got {type(tools).__name__}")
    return list(tools)


def _looks_like_inprocess(name: str) -> bool:
    # WorkIQ toolbox tools always contain a `Server___Tool` triple-underscore
    # separator. Anything without it that's a bare snake_case identifier is
    # almost certainly meant to be in-process.
    return "___" not in name and name.replace("_", "").isalnum()


def _build_bundle(skill: Skill) -> SkillBundle:
    domain_tools = _load_skill_domain_tools(skill.name)
    # Phase skills nested under a parent (e.g. sow-response/reply-poll/) also
    # inherit the parent skill's domain tools so they can use shared tools like
    # dashboard_payload without duplicating the Python module.
    _root = skills_dir().resolve()
    if skill.path.parent.parent.resolve() != _root:
        parent_skill_name = skill.path.parent.parent.name
        existing_names = {_tool_name(fn) for fn in domain_tools}
        for fn in _load_skill_domain_tools(parent_skill_name):
            if _tool_name(fn) not in existing_names:
                domain_tools.append(fn)
                existing_names.add(_tool_name(fn))
    available: dict[str, Any] = dict(_TOOL_REGISTRY)
    for fn in domain_tools:
        available[_tool_name(fn)] = fn

    if not skill.allowed_tools:
        return SkillBundle(
            manifest=skill,
            inprocess_tools=tuple(available.values()),
            toolbox_allowlist=None,
        )

    inprocess: list[Any] = []
    external_literals: list[str] = []
    has_glob = False
    for name in skill.allowed_tools:
        if "*" in name:
            has_glob = True
            continue
        if name in available:
            inprocess.append(available[name])
        elif _looks_like_inprocess(name):
            raise ValueError(
                f"skill {skill.name!r}: allowed-tools entry {name!r} "
                f"looks like an in-process tool but is not registered. "
                f"Known: {sorted(available)}"
            )
        else:
            external_literals.append(name)

    allowlist: tuple[str, ...] | None
    if has_glob or not external_literals:
        allowlist = None
    else:
        allowlist = tuple(external_literals)

    return SkillBundle(
        manifest=skill,
        inprocess_tools=tuple(inprocess),
        toolbox_allowlist=allowlist,
    )


def skills_dir() -> Path:
    override = os.environ.get("CHARTER_AGENT_SKILLS_DIR")
    if override:
        return Path(override)
    # parents: runtime → charter_agent → src → agent. Skill folders live under agent/skills/.
    return Path(__file__).resolve().parents[3] / "skills"


def load_all() -> list[Skill]:
    """Parse every SKILL.md manifest. Does not build bundles."""
    root = skills_dir()
    _skill_cache.clear()
    _bundle_cache.clear()
    if not root.exists():
        return []
    skills: list[Skill] = []
    for skill_md in sorted(root.glob("**/SKILL.md")):
        try:
            skill = _parse(skill_md)
        except Exception as exc:  # noqa: BLE001
            log.warning("skill_loader: skipping %s (%s)", skill_md, exc)
            continue
        if skill.name in _skill_cache:
            log.warning(
                "skill_loader: duplicate name %r at %s (already loaded from %s); skipping",
                skill.name, skill_md, _skill_cache[skill.name].path,
            )
            continue
        _skill_cache[skill.name] = skill
        skills.append(skill)
    return skills


def load_bundles() -> dict[str, SkillBundle]:
    """Resolve every parsed skill into a SkillBundle. Strict-validates
    allowed-tools per the policy in the module docstring."""
    if not _skill_cache:
        load_all()
    _bundle_cache.clear()
    for name, skill in _skill_cache.items():
        bundle = _build_bundle(skill)
        _bundle_cache[name] = bundle
        log.info(
            "skill %r: %d in-process tool(s), toolbox=%s",
            name,
            len(bundle.inprocess_tools),
            "filtered" if bundle.toolbox_allowlist else "unrestricted",
        )
    return dict(_bundle_cache)


def get(name: str) -> Skill:
    if not _skill_cache:
        load_all()
    try:
        return _skill_cache[name]
    except KeyError as e:
        raise KeyError(f"skill_loader: no skill named {name!r} (have {list(_skill_cache)})") from e


def get_bundle(name: str) -> SkillBundle:
    if not _bundle_cache:
        load_bundles()
    try:
        return _bundle_cache[name]
    except KeyError as e:
        raise KeyError(f"skill_loader: no bundle named {name!r} (have {list(_bundle_cache)})") from e


def loaded_names() -> list[str]:
    """Return names of already-loaded skills without triggering a reload."""
    return list(_skill_cache)
