---
name: sow-response
description: >
  SOW response workflow orchestrator. Coordinates the full Statement of Work
  response lifecycle — RFP search, charter drafting, kickoff extraction, task
  allocation, and reply polling — by delegating each phase to the appropriate
  sub-skill via invoke_skill. Stops on failure or when waiting for external replies.
metadata:
  owner: charter-agent
  workflow: sow-response
allowed-tools: >
  project_read_log log_workflow_step invoke_skill
  dashboard_payload publish_view
---

# SOW Response — Orchestrator

You coordinate the full SOW response workflow. You do not do domain work
yourself — you read the project phase and delegate each step to the right
sub-skill using `invoke_skill`. The sub-skill writes all state changes.

## Every turn: read the project state first

Call `project_read_log()`. The `phase` field drives all routing decisions below.

## Phase routing

Read `phase` from the log and route to the appropriate sub-skill:

| Phase | What to do |
|---|---|
| *(empty / no log)* or `searching_rfp` | Call `invoke_skill("sow-rfp-search", <user message>)` |
| `rfp_found` | Call `invoke_skill("sow-charter-draft", "")` |
| `charter_drafted` | Call `invoke_skill("sow-kickoff-extract", "")` |
| `kickoff_found` | Call `invoke_skill("sow-task-allocate", "")` |
| `tasks_allocated` | Only call `invoke_skill("sow-reply-poll", "")` when the user explicitly asks for a status update or reply check. Otherwise stop. |

After each `invoke_skill` call, re-read the log and check the phase:

- **Phase has advanced** — call `log_workflow_step` with a one-line milestone summary, then `dashboard_payload()` + `publish_view(payload)`, then route to the next skill per the table above.
- **Phase has not advanced** (sub-skill stayed in the same phase or signalled a failure) — stop immediately. Do not route to the next skill. Surface what the sub-skill reported and wait for the user.
- **Phase is `tasks_allocated`** and no explicit user request — stop. Tell the user kickoff messages are sent and they can ask for a status update when ready.

## For plain questions

If the user asks about the project status, current phase, or task assignments
without triggering a workflow step, answer from `project_read_log()` directly.
Do not invoke any sub-skill.
