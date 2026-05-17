from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from charter_agent import orchestrator
from charter_agent.charter.schemas import (
    Charter,
    Deliverable,
    GroundingSource,
    Stakeholders,
    Task,
    WatchChannel,
)
from charter_agent.runtime import foundry_host, skill_loader


@pytest.fixture(autouse=True)
def real_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the loader to look at the real agent/skills/ directory.
    skill_loader._cache.clear()
    monkeypatch.delenv("CHARTER_AGENT_SKILLS_DIR", raising=False)


def test_project_kickoff_skill_loads() -> None:
    skill = skill_loader.get("project-kickoff")
    assert skill.name == "project-kickoff"
    assert "Charter" in skill.body
    assert len(skill.description) > 100


async def test_propose_charter_requires_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(foundry_host, "_chat_agent", object())
    monkeypatch.setattr(foundry_host, "_toolbox", object())
    r = await orchestrator.handle_invocation("propose_charter", {})
    assert not r["ok"]
    assert "prompt" in r["error"]


async def test_propose_charter_happy_path(
    monkeypatch: pytest.MonkeyPatch, isolated_home
) -> None:
    monkeypatch.setattr(foundry_host, "_chat_agent", object())
    monkeypatch.setattr(foundry_host, "_toolbox", object())
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", "proj-lumen")

    proposed = Charter(
        project_id="proj-lumen",
        project_kind="board_pack",
        stakeholders=Stakeholders(
            coordinator="priya@example.com",
            deputy="cos@example.com",
            owners=["finance@example.com"],
        ),
        tasks=[
            Task(
                task_id="finance-section",
                title="Finance section",
                owner_upn="finance@example.com",
            )
        ],
        watch_channels=[
            WatchChannel(kind="outlook_inbox", config={"upn": "priya@example.com"})
        ],
        deliverable=Deliverable(output_location="/sites/board/2026-05", format="word"),
        grounding_sources=[
            GroundingSource(
                kind="email",
                ref="AAMkAGI=",
                summary="Priya's kickoff email cited as primary source.",
                used=True,
            )
        ],
    )

    class _FakeRunResult:
        value = proposed

    monkeypatch.setattr(
        foundry_host, "run_skill", AsyncMock(return_value=_FakeRunResult())
    )

    r = await orchestrator.handle_invocation(
        "propose_charter",
        {"prompt": "Spin up board pack for May meeting; see Priya's email."},
        visitor_identity={"upn": "priya@example.com"},
    )
    assert r["ok"]
    pc = r["result"]["proposed_charter"]
    assert pc["project_id"] == "proj-lumen"
    assert pc["version"] == 1
    assert pc["ratified_at"] is None  # ratification is a separate verb


async def test_propose_charter_parses_json_text(
    monkeypatch: pytest.MonkeyPatch, isolated_home
) -> None:
    monkeypatch.setattr(foundry_host, "_chat_agent", object())
    monkeypatch.setattr(foundry_host, "_toolbox", object())

    proposed = Charter(
        project_id="proj-x",
        project_kind="other",
        stakeholders=Stakeholders(
            coordinator="a@example.com",
            deputy="b@example.com",
            owners=["c@example.com"],
        ),
        tasks=[Task(task_id="t", title="T", owner_upn="c@example.com")],
        deliverable=Deliverable(output_location="/path", format="markdown"),
    )

    class _FakeRunResult:
        text = proposed.model_dump_json()

    monkeypatch.setattr(
        foundry_host, "run_skill", AsyncMock(return_value=_FakeRunResult())
    )

    r = await orchestrator.handle_invocation(
        "propose_charter",
        {"prompt": "Anything."},
        visitor_identity={"upn": "a@example.com"},
    )
    assert r["ok"]
    assert r["result"]["proposed_charter"]["project_id"] == "proj-x"
