"""Pydantic schemas for the Project Charter and related contracts.

Authoritative shape per AGENTS.md §11.4 and architecture §5. Charter is the
immutable per-project contract; only the kickoff and amend code paths may write
it. State, suggested-action, and candidate-event schemas will land alongside the
modules that own them (Phase 4+).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

ChannelKind = Literal[
    "sharepoint_folder",
    "teams_channel",
    "teams_chat",
    "outlook_inbox",
    "outlook_tasks",
    "onedrive_folder",
]

TaskStatusHint = Literal["Assigned", "InProgress", "Submitted"]
DeliverableFormat = Literal["word", "excel", "pdf", "markdown"]


class Stakeholders(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coordinator: str = Field(..., description="UPN of the project coordinator (ratifier).")
    deputy: str = Field(..., description="UPN used when coordinator's token is unavailable.")
    owners: list[str] = Field(default_factory=list)
    observers: list[str] = Field(default_factory=list)

    @field_validator("coordinator", "deputy")
    @classmethod
    def _upn_shape(cls, v: str) -> str:
        if "@" not in v or not v.strip():
            raise ValueError(f"not a UPN: {v!r}")
        return v.strip().lower()


class WatchChannel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ChannelKind
    config: dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    owner_upn: str
    due_at: datetime | None = None
    runbook_requirements: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    initial_status: TaskStatusHint = "Assigned"

    @field_validator("owner_upn")
    @classmethod
    def _upn_shape(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError(f"not a UPN: {v!r}")
        return v.strip().lower()


class ConsolidationRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_path: str | None = None
    section_order: list[str] = Field(default_factory=list)
    cross_section_checks: list[str] = Field(default_factory=list)
    notes: str = ""


class Deliverable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_location: str = Field(..., description="SharePoint/OneDrive path for the final artifact.")
    format: DeliverableFormat


class GroundingSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["email", "meeting", "file", "teams_message", "runbook", "other"]
    ref: str = Field(..., description="WorkIQ resource id / URL / message id.")
    summary: str = ""
    used: bool = True


class Charter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(..., min_length=1, max_length=64)
    version: int = Field(default=1, ge=1)
    project_kind: str = Field(
        ...,
        description="Free-text label — e.g. 'board_pack', 'audit', 'campaign', 'tender_response'.",
    )
    stakeholders: Stakeholders
    tasks: list[Task] = Field(default_factory=list)
    watch_channels: list[WatchChannel] = Field(default_factory=list)
    consolidation_rules: ConsolidationRules = Field(default_factory=ConsolidationRules)
    deliverable: Deliverable
    consolidator_module_path: str | None = None
    grounding_sources: list[GroundingSource] = Field(default_factory=list)
    ratified_at: datetime | None = None
    ratified_by: str | None = None  # UPN of the coordinator at ratification time

    def is_ratified(self) -> bool:
        return self.ratified_at is not None and self.ratified_by is not None


class AmendmentSpec(BaseModel):
    """A coordinator-proposed change to a ratified Charter.

    The body is intentionally loose at the schema layer — the amend-charter skill
    is what reasons over the change shape and produces the next Charter version.
    """

    model_config = ConfigDict(extra="forbid")
    amendment_id: UUID
    reason: str = Field(..., min_length=1)
    changes: dict[str, Any]  # opaque to the schema layer; skill interprets


__all__ = [
    "AmendmentSpec",
    "Charter",
    "ConsolidationRules",
    "Deliverable",
    "GroundingSource",
    "Stakeholders",
    "Task",
    "WatchChannel",
]
