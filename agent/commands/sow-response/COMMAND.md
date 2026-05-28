---
name: sow-response
description: >
  SOW response coordination workflow — from RFP discovery through charter
  drafting, kickoff extraction, task allocation, and reply tracking.
entry_skill: sow-rfp-search
phases:
  searching_rfp:
    skill: sow-rfp-search
    auto_advance: false
  rfp_found:
    skill: sow-charter-draft
    auto_advance: true
  charter_drafted:
    skill: sow-kickoff-extract
    auto_advance: true
  kickoff_found:
    skill: sow-task-allocate
    auto_advance: false
  tasks_allocated:
    skill: sow-reply-poll
    auto_advance: false
---

# SOW Response — Workflow Command

This command orchestrates the full SOW response process. The host routes each
turn to the skill that owns the project's current phase. Adding a new phase
means adding an entry here and a matching SKILL.md — no host code changes.

## Phase sequence

| Phase | Skill | Auto-advances? |
|---|---|---|
| `searching_rfp` | `sow-rfp-search` | No — halts if RFP not found |
| `rfp_found` | `sow-charter-draft` | Yes — no user decision needed |
| `charter_drafted` | `sow-kickoff-extract` | Yes — no user decision needed |
| `kickoff_found` | `sow-task-allocate` | No — user confirms before messages sent |
| `tasks_allocated` | `sow-reply-poll` | No — user triggers each poll |
