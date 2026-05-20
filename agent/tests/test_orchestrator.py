from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from charter_agent import orchestrator
from charter_agent.runtime import foundry_host, skill_loader


@pytest.fixture(autouse=True)
def fake_host(monkeypatch: pytest.MonkeyPatch) -> None:
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


# --- run_skill ------------------------------------------------------------


class _FakeRunResult:
    def __init__(self, text: str) -> None:
        self.text = text


async def test_run_skill_dispatches_to_named_skill(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_skill = skill_loader.Skill(
        name="sow-response",
        description="...",
        body="SKILL BODY",
        path=isolated_home / "fake.md",
        allowed_tools=None,
    )
    monkeypatch.setattr(skill_loader, "get", lambda _name: fake_skill)

    captured: dict[str, Any] = {}

    async def _fake_run(*, skill_body: str, user_prompt: str, session_id: str | None) -> Any:
        captured["skill_body"] = skill_body
        captured["user_prompt"] = user_prompt
        captured["session_id"] = session_id
        return _FakeRunResult("the skill said hello")

    monkeypatch.setattr(foundry_host, "run_skill", AsyncMock(side_effect=_fake_run))

    r = await orchestrator.handle_invocation(
        "run_skill",
        {"skill_name": "sow-response", "prompt": "do the thing"},
        {"upn": "carla@example.com"},
    )
    assert r["ok"]
    assert r["result"]["skill_name"] == "sow-response"
    assert r["result"]["session_id"] == "test-project"
    assert r["result"]["response_text"] == "the skill said hello"

    assert captured["skill_body"] == "SKILL BODY"
    assert "caller_upn: carla@example.com" in captured["user_prompt"]
    assert "session_id: test-project" in captured["user_prompt"]
    assert "do the thing" in captured["user_prompt"]

    activity = (isolated_home / "activity.json").read_text(encoding="utf-8").splitlines()
    kinds = [line for line in activity]
    assert any('"kind": "run_skill_start"' in k for k in kinds)
    assert any('"kind": "run_skill_end"' in k for k in kinds)


async def test_run_skill_validates_payload(isolated_home: Path) -> None:
    r1 = await orchestrator.handle_invocation("run_skill", {"prompt": "x"})
    assert r1["ok"] is False
    assert "skill_name" in r1["error"]

    r2 = await orchestrator.handle_invocation("run_skill", {"skill_name": "sow-response"})
    assert r2["ok"] is False
    assert "prompt" in r2["error"]


async def test_run_skill_unknown_skill_returns_error(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(_name: str) -> Any:
        raise KeyError("skill 'mystery' not found")

    monkeypatch.setattr(skill_loader, "get", _raise)
    r = await orchestrator.handle_invocation(
        "run_skill", {"skill_name": "mystery", "prompt": "x"}
    )
    assert r["ok"] is False
    assert "mystery" in r["error"]
