from __future__ import annotations

import json
from pathlib import Path

import pytest

from charter_agent import state


def test_write_text_is_atomic(isolated_home: Path) -> None:
    state.write_text("notes/a.md", "hello")
    assert (isolated_home / "notes" / "a.md").read_text(encoding="utf-8") == "hello"
    state.write_text("notes/a.md", "world")
    assert (isolated_home / "notes" / "a.md").read_text(encoding="utf-8") == "world"
    assert not (isolated_home / "notes" / "a.md.tmp").exists()


def test_write_json_round_trip(isolated_home: Path) -> None:
    state.write_json("project_log.json", {"tasks": [{"id": "t1"}]})
    assert state.read_json("project_log.json") == {"tasks": [{"id": "t1"}]}
    raw = json.loads((isolated_home / "project_log.json").read_text(encoding="utf-8"))
    assert raw == {"tasks": [{"id": "t1"}]}


def test_append_ndjson(isolated_home: Path) -> None:
    state.append_ndjson("activity.json", {"k": "a"})
    state.append_ndjson("activity.json", {"k": "b"})
    lines = (isolated_home / "activity.json").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"k": "a"}, {"k": "b"}]


def test_list_files_returns_relative_paths(isolated_home: Path) -> None:
    state.write_text("a.md", "x")
    state.write_text("sub/b.md", "y")
    assert state.list_files() == ["a.md", "sub/b.md"]
    assert state.list_files("sub") == ["sub/b.md"]
    assert state.list_files("missing") == []


def test_exists(isolated_home: Path) -> None:
    assert state.exists("nope") is False
    state.write_text("yep.md", "x")
    assert state.exists("yep.md") is True


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "  ",
        "../escape",
        "sub/../../escape",
        "/abs/path",
    ],
)
def test_path_validation_rejects_escape(bad: str) -> None:
    with pytest.raises(ValueError):
        state.write_text(bad, "x")


def test_read_missing_raises(isolated_home: Path) -> None:
    with pytest.raises(FileNotFoundError):
        state.read_text("nope.md")
