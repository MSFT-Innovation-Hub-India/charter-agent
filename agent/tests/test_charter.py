from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from charter_agent.charter import amend, ratify, read_charter
from charter_agent.charter.schemas import (
    AmendmentSpec,
    Charter,
    Deliverable,
    Stakeholders,
    Task,
    WatchChannel,
)


def _minimal_charter(**overrides) -> Charter:
    base = dict(
        project_id="proj-lumen",
        version=1,
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
                runbook_requirements=["Includes a 12-month variance table"],
            )
        ],
        watch_channels=[
            WatchChannel(kind="outlook_inbox", config={"upn": "priya@example.com"})
        ],
        deliverable=Deliverable(output_location="/sites/board/2026-05", format="word"),
    )
    base.update(overrides)
    return Charter(**base)


def test_charter_round_trip() -> None:
    c = _minimal_charter()
    j = c.model_dump_json()
    c2 = Charter.model_validate_json(j)
    assert c == c2


def test_charter_normalises_upns() -> None:
    c = _minimal_charter(
        stakeholders=Stakeholders(
            coordinator=" PRIYA@example.com ",
            deputy="COS@example.com",
            owners=["Finance@Example.com"],
        ),
        tasks=[Task(task_id="t", title="T", owner_upn="finance@example.com")],
    )
    assert c.stakeholders.coordinator == "priya@example.com"
    assert c.stakeholders.deputy == "cos@example.com"


def test_ratify_writes_and_stamps(isolated_home) -> None:
    c = _minimal_charter()
    assert not c.is_ratified()

    r = ratify(c, by_upn="priya@example.com")
    assert r.is_ratified()
    assert r.ratified_by == "priya@example.com"
    assert isinstance(r.ratified_at, datetime) and r.ratified_at.tzinfo == timezone.utc

    loaded = read_charter()
    assert loaded == r


def test_ratify_rejects_double(isolated_home) -> None:
    r = ratify(_minimal_charter(), by_upn="priya@example.com")
    with pytest.raises(ValueError, match="already ratified"):
        ratify(r, by_upn="priya@example.com")


def test_ratify_rejects_duplicate_task_id(isolated_home) -> None:
    c = _minimal_charter(
        tasks=[
            Task(task_id="t", title="A", owner_upn="finance@example.com"),
            Task(task_id="t", title="B", owner_upn="finance@example.com"),
        ]
    )
    with pytest.raises(ValueError, match="duplicate task_id"):
        ratify(c, by_upn="priya@example.com")


def test_ratify_rejects_orphan_dependency(isolated_home) -> None:
    c = _minimal_charter(
        tasks=[
            Task(
                task_id="a",
                title="A",
                owner_upn="finance@example.com",
                depends_on=["missing"],
            )
        ]
    )
    with pytest.raises(ValueError, match="depends on unknown"):
        ratify(c, by_upn="priya@example.com")


def test_ratify_rejects_owner_not_in_stakeholders(isolated_home) -> None:
    c = _minimal_charter(
        tasks=[Task(task_id="t", title="T", owner_upn="stranger@example.com")]
    )
    with pytest.raises(ValueError, match="not in stakeholders"):
        ratify(c, by_upn="priya@example.com")


def test_amend_bumps_version(isolated_home) -> None:
    current = ratify(_minimal_charter(), by_upn="priya@example.com")

    new = _minimal_charter(
        version=2,
        tasks=[
            Task(task_id="finance-section", title="Finance section", owner_upn="finance@example.com"),
            Task(task_id="risk-section", title="Risk section", owner_upn="finance@example.com"),
        ],
    )
    amendment = AmendmentSpec(
        amendment_id=uuid4(),
        reason="Add risk section per audit committee request.",
        changes={"add_tasks": ["risk-section"]},
    )

    next_charter = amend(current, amendment, new, by_upn="priya@example.com")
    assert next_charter.version == 2
    assert {t.task_id for t in next_charter.tasks} == {"finance-section", "risk-section"}
    assert read_charter().version == 2


def test_amend_rejects_wrong_version_bump(isolated_home) -> None:
    current = ratify(_minimal_charter(), by_upn="priya@example.com")
    new = _minimal_charter(version=5)  # wrong
    amendment = AmendmentSpec(amendment_id=uuid4(), reason="r", changes={})
    with pytest.raises(ValueError, match="must bump version to 2"):
        amend(current, amendment, new, by_upn="priya@example.com")


def test_amend_rejects_project_id_change(isolated_home) -> None:
    current = ratify(_minimal_charter(), by_upn="priya@example.com")
    new = _minimal_charter(version=2, project_id="different")
    amendment = AmendmentSpec(amendment_id=uuid4(), reason="r", changes={})
    with pytest.raises(ValueError, match="project_id is immutable"):
        amend(current, amendment, new, by_upn="priya@example.com")
