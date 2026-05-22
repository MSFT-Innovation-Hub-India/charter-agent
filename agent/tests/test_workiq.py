from __future__ import annotations

import pytest

from charter_agent import workiq
from charter_agent.runtime import foundry_host


@pytest.fixture(autouse=True)
def fake_toolbox(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hosted MCP tool spec is opaque to client code; a sentinel is enough
    # to satisfy the boot-state check inside `list_available_tools`.
    monkeypatch.setattr(foundry_host, "_chat_client", object())
    monkeypatch.setattr(foundry_host, "_mcp_tool", object())
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


async def test_list_available_tools_returns_server_summary() -> None:
    tools = await workiq.list_available_tools()
    servers = {t["server"] for t in tools}
    assert servers == set(workiq.WORKIQ_SERVERS)
    # Per-tool catalog is unavailable on the hosted MCP path.
    assert all(t["name"] is None for t in tools)
