from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from charter_agent import workiq


class _T:
    def __init__(self, name: str) -> None:
        self.name = name
        self.server_name = "ServerX"
        self.description = "..."


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    workiq._tool_name_cache.clear()


async def test_resolve_tool_exact_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workiq,
        "list_available_tools",
        AsyncMock(return_value=[{"name": "send_mail"}, {"name": "post_message"}]),
    )
    n = await workiq.resolve_tool("send_mail", ["send_email", "send_mail"])
    assert n == "send_mail"
    # Cached.
    assert workiq._tool_name_cache["send_mail"] == "send_mail"


async def test_resolve_tool_namespaced_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workiq,
        "list_available_tools",
        AsyncMock(
            return_value=[
                {"name": "WorkIQMail2_send_mail"},
                {"name": "WorkIQTeams_post_channel_message"},
            ]
        ),
    )
    n = await workiq.resolve_tool("send_mail", ["send_mail"])
    assert n == "WorkIQMail2_send_mail"


async def test_resolve_tool_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workiq,
        "list_available_tools",
        AsyncMock(return_value=[{"name": "unrelated_tool"}]),
    )
    with pytest.raises(RuntimeError, match="no Toolbox tool matched"):
        await workiq.resolve_tool("send_mail", ["send_mail", "send_email"])
