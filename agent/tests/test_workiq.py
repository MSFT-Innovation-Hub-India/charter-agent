from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from charter_agent import orchestrator, workiq
from charter_agent.runtime import foundry_host


class _FakeTool:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description


@pytest.fixture(autouse=True)
def fake_toolbox(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock()
    fake.connect = AsyncMock(return_value=None)
    fake.functions = [
        _FakeTool("WorkIQMail2___SearchMessages", "Search mail"),
        _FakeTool("WorkIQTeams___SendMessageToChannel", "Post a Teams message"),
    ]
    monkeypatch.setattr(foundry_host, "_chat_agent", object())
    monkeypatch.setattr(foundry_host, "_toolbox", fake)
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", "test-project")


def test_workiq_servers_match_portal() -> None:
    assert set(workiq.WORKIQ_SERVERS) == {
        "WorkIQMail2",
        "WorkIQTeams",
        "WorkIQCalendar2",
        "WorkIQSharePoint2",
        "WorkIQOneDrive",
        "WorkIQWord",
        "WorkIQUser",
        "WorkIQCopilot",
    }


async def test_list_tools_verb_returns_toolbox_catalog() -> None:
    r: dict[str, Any] = await orchestrator.handle_invocation("list_tools", {})
    assert r["ok"]
    assert r["result"]["tool_count"] == 2
    names = {t["name"] for t in r["result"]["tools"]}
    assert names == {"WorkIQMail2___SearchMessages", "WorkIQTeams___SendMessageToChannel"}
