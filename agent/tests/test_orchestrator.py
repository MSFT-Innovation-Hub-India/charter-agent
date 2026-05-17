from __future__ import annotations

from pathlib import Path

import pytest

from charter_agent import orchestrator
from charter_agent.runtime import foundry_host


@pytest.fixture(autouse=True)
def fake_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # Phase 1: skip real ChatAgent wiring in unit tests.
    monkeypatch.setattr(foundry_host, "_chat_agent", object())
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", "test-project")


async def test_echo_increments_counter(isolated_home: Path) -> None:
    r1 = await orchestrator.handle_invocation("echo", {"message": "hi"})
    r2 = await orchestrator.handle_invocation("echo", {"message": "again"})
    assert r1["ok"] and r2["ok"]
    assert r1["result"]["count"] == 1
    assert r2["result"]["count"] == 2
    assert r2["result"]["session_id"] == "test-project"


async def test_echo_records_activity(isolated_home: Path) -> None:
    await orchestrator.handle_invocation("echo", {"message": "x"}, {"upn": "alice@example.com"})
    activity = (isolated_home / "activity.json").read_text().splitlines()
    assert len(activity) == 1
    assert '"actor": "alice@example.com"' in activity[0]


async def test_unknown_action_returns_error(isolated_home: Path) -> None:
    r = await orchestrator.handle_invocation("nope", {})
    assert r["ok"] is False
    assert "unknown action" in r["error"]
