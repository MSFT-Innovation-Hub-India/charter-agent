from __future__ import annotations

from pathlib import Path

import pytest

from charter_agent.runtime import skill_loader


def _write(root: Path, name: str, frontmatter: str, body: str = "do the thing") -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")


def test_empty_dir_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTER_AGENT_SKILLS_DIR", str(tmp_path))
    assert skill_loader.load_all() == []


def test_loads_valid_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTER_AGENT_SKILLS_DIR", str(tmp_path))
    _write(tmp_path, "do-thing", "name: do-thing\ndescription: Does the thing when asked.")
    skills = skill_loader.load_all()
    assert len(skills) == 1
    assert skills[0].name == "do-thing"


def test_rejects_mismatched_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTER_AGENT_SKILLS_DIR", str(tmp_path))
    _write(tmp_path, "do-thing", "name: other\ndescription: hi")
    with pytest.raises(ValueError, match="must equal parent dir"):
        skill_loader.load_all()


def test_rejects_missing_frontmatter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTER_AGENT_SKILLS_DIR", str(tmp_path))
    d = tmp_path / "x"
    d.mkdir()
    (d / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        skill_loader.load_all()
