from __future__ import annotations

from pathlib import Path

import pytest

from charter_agent.runtime import state_tools


def test_describe_tools_lists_expected_set() -> None:
    names = {t["name"] for t in state_tools.describe_tools()}
    assert names == {
        "state_write_text",
        "state_read_text",
        "state_write_json",
        "state_read_json",
        "state_list_files",
        "state_file_exists",
        "log_workflow_step",
    }


def test_state_tools_are_maf_function_tools() -> None:
    # Each entry must be a callable MAF tool (has a `.name` attribute MAF sets).
    for fn in state_tools.STATE_TOOLS:
        assert hasattr(fn, "name"), f"{fn} is not a MAF tool"


def _invoke(tool: object, /, **kwargs: object) -> object:
    """Call a `@tool`-decorated callable's underlying Python function.

    MAF's `@tool` returns a `FunctionTool` wrapper; the original sync/async
    function is exposed as `.func`. We sidestep the MAF invocation machinery
    in tests by calling that directly.
    """
    func = getattr(tool, "func", None) or tool
    assert callable(func)
    return func(**kwargs)


def test_state_write_and_read_text(isolated_home: Path) -> None:
    msg = _invoke(state_tools.state_write_text, path="hello.md", content="hi")
    assert "hello.md" in str(msg)
    assert (isolated_home / "hello.md").read_text(encoding="utf-8") == "hi"
    assert _invoke(state_tools.state_read_text, path="hello.md") == "hi"


def test_state_write_and_read_json(isolated_home: Path) -> None:
    _invoke(state_tools.state_write_json, path="log.json", obj={"a": 1})
    assert _invoke(state_tools.state_read_json, path="log.json") == {"a": 1}


def test_log_workflow_step_appends_activity(isolated_home: Path) -> None:
    _invoke(state_tools.log_workflow_step, kind="grounded", summary="ran copilot_chat", ref="")
    lines = (isolated_home / "activity.json").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert '"kind": "grounded"' in lines[0]


def test_path_validation_via_tool(isolated_home: Path) -> None:
    with pytest.raises(ValueError):
        _invoke(state_tools.state_write_text, path="../escape", content="x")
