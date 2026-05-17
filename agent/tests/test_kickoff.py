from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from charter_agent import kickoff, orchestrator, state, workiq
from charter_agent.charter import ratify, read_charter
from charter_agent.charter.schemas import (
    Charter,
    Deliverable,
    Stakeholders,
    Task,
    WatchChannel,
)
from charter_agent.runtime import foundry_host


def _charter() -> Charter:
    return Charter(
        project_id="proj-lumen",
        project_kind="board_pack",
        stakeholders=Stakeholders(
            coordinator="priya@example.com",
            deputy="cos@example.com",
            owners=["finance@example.com", "risk@example.com"],
        ),
        tasks=[
            Task(
                task_id="finance-section",
                title="Finance section",
                owner_upn="finance@example.com",
                runbook_requirements=["12-month variance table"],
                due_at=datetime(2026, 5, 28, 17, 0, tzinfo=timezone.utc),
            ),
            Task(
                task_id="risk-section",
                title="Risk section",
                owner_upn="risk@example.com",
            ),
        ],
        watch_channels=[
            WatchChannel(
                kind="teams_channel",
                config={"team_id": "T1", "channel_id": "C1"},
            ),
            WatchChannel(kind="outlook_inbox", config={"upn": "priya@example.com"}),
        ],
        deliverable=Deliverable(
            output_location="/sites/board/2026-05", format="word"
        ),
    )


@pytest.fixture
def mocked_workiq(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    workiq._tool_name_cache.clear()
    mocks = {
        "send_mail": AsyncMock(return_value={"id": "m1"}),
        "post_teams_message": AsyncMock(return_value={"id": "tm1"}),
        "create_sharepoint_folder": AsyncMock(return_value={"id": "f1"}),
        "create_outlook_task": AsyncMock(return_value={"id": "t1"}),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(workiq, name, m)
    return mocks


async def test_fanout_executes_all_steps(
    isolated_home, mocked_workiq: dict[str, AsyncMock]
) -> None:
    c = _charter()
    summary = await kickoff.fanout(c, by_upn="priya@example.com")

    assert summary["sharepoint_folder"]["status"] == "created"
    assert summary["teams_kickoff"]["status"] == "posted"
    assert summary["briefing_emails"]["sent"] == 2
    assert summary["outlook_tasks"]["created"] == 2

    mocked_workiq["create_sharepoint_folder"].assert_awaited_once()
    mocked_workiq["post_teams_message"].assert_awaited_once()
    assert mocked_workiq["send_mail"].await_count == 2
    assert mocked_workiq["create_outlook_task"].await_count == 2


async def test_fanout_is_idempotent(
    isolated_home, mocked_workiq: dict[str, AsyncMock]
) -> None:
    c = _charter()
    await kickoff.fanout(c, by_upn="priya@example.com")
    # Second run must not re-send anything.
    summary = await kickoff.fanout(c, by_upn="priya@example.com")

    assert summary["sharepoint_folder"]["status"] == "already_done"
    assert summary["teams_kickoff"]["status"] == "already_done"
    assert summary["briefing_emails"]["sent"] == 0  # nothing new sent
    assert summary["outlook_tasks"]["created"] == 0

    # Underlying mocks were still called exactly once per item in run 1.
    assert mocked_workiq["create_sharepoint_folder"].await_count == 1
    assert mocked_workiq["post_teams_message"].await_count == 1
    assert mocked_workiq["send_mail"].await_count == 2
    assert mocked_workiq["create_outlook_task"].await_count == 2


async def test_fanout_skips_teams_when_no_channel_watcher(
    isolated_home, mocked_workiq: dict[str, AsyncMock]
) -> None:
    c = _charter().model_copy(
        update={
            "watch_channels": [
                WatchChannel(kind="outlook_inbox", config={"upn": "priya@example.com"})
            ]
        }
    )
    summary = await kickoff.fanout(c, by_upn="priya@example.com")
    assert summary["teams_kickoff"]["status"] == "skipped"
    mocked_workiq["post_teams_message"].assert_not_awaited()


async def test_fanout_records_email_failure_but_continues(
    isolated_home, mocked_workiq: dict[str, AsyncMock]
) -> None:
    c = _charter()
    mocked_workiq["send_mail"].side_effect = [
        RuntimeError("smtp boom"),
        {"id": "m2"},
    ]
    summary = await kickoff.fanout(c, by_upn="priya@example.com")
    assert summary["briefing_emails"]["sent"] == 1
    assert len(summary["briefing_emails"]["failures"]) == 1
    assert "smtp" in summary["briefing_emails"]["failures"][0]["error"]


async def test_ratify_verb_persists_and_fans_out(
    isolated_home,
    mocked_workiq: dict[str, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(foundry_host, "_chat_agent", object())
    monkeypatch.setattr(foundry_host, "_toolbox", object())

    r = await orchestrator.handle_invocation(
        "ratify_charter",
        {"charter": _charter().model_dump(mode="json")},
        visitor_identity={"upn": "priya@example.com"},
    )
    assert r["ok"], r
    assert r["result"]["charter"]["ratified_by"] == "priya@example.com"
    assert r["result"]["fanout"]["briefing_emails"]["sent"] == 2

    # Charter was persisted.
    persisted = read_charter()
    assert persisted is not None and persisted.is_ratified()

    # State tracks fanout idempotency keys.
    s = state.read_state()
    assert s["kickoff"]["sharepoint_folder_done"] is True
    assert set(s["kickoff"]["briefing_emails_sent"]) == {
        "finance@example.com",
        "risk@example.com",
    }


async def test_ratify_verb_rejects_invalid_charter(
    isolated_home, mocked_workiq: dict[str, AsyncMock]
) -> None:
    r = await orchestrator.handle_invocation(
        "ratify_charter",
        {"charter": {"project_id": "p"}},  # missing required fields
        visitor_identity={"upn": "priya@example.com"},
    )
    assert not r["ok"]
    assert "validation failed" in r["error"]


async def test_fanout_skipped_when_capability_unavailable(
    isolated_home, mocked_workiq: dict[str, AsyncMock]
) -> None:
    # Both SharePoint folder creation and Outlook tasks raise NotImplementedError
    # against the real Toolbox (no resolver / no Tasks server). Fanout should
    # report them as skipped without polluting failure lists.
    c = _charter()
    mocked_workiq["create_sharepoint_folder"].side_effect = NotImplementedError(
        "needs resolver"
    )
    mocked_workiq["create_outlook_task"].side_effect = NotImplementedError(
        "no Tasks server"
    )

    summary = await kickoff.fanout(c, by_upn="priya@example.com")

    assert summary["sharepoint_folder"]["status"] == "skipped"
    assert "resolver" in summary["sharepoint_folder"]["reason"]
    assert summary["outlook_tasks"]["status"] == "skipped"
    assert summary["outlook_tasks"]["created"] == 0
    assert summary["outlook_tasks"]["failures"] == []
    # Email and Teams still ran.
    assert summary["briefing_emails"]["sent"] == 2
    assert summary["teams_kickoff"]["status"] == "posted"
