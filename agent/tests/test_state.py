from __future__ import annotations

import json
from pathlib import Path

from charter_agent import state


def test_counter_starts_at_zero_and_bumps(isolated_home: Path) -> None:
    assert state.read_state() == {}
    assert state.bump_counter() == 1
    assert state.bump_counter() == 2
    assert state.read_state()["counter"] == 2


def test_write_is_atomic(isolated_home: Path) -> None:
    state.write_state({"a": 1})
    assert json.loads((isolated_home / "state.json").read_text()) == {"a": 1}
    state.write_state({"a": 2})
    assert json.loads((isolated_home / "state.json").read_text()) == {"a": 2}
    assert not (isolated_home / "state.json.tmp").exists()
