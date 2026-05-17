---
name: status-refresh
description: Use this skill when the dashboard is being rendered or the coordinator asks for a fresh status read. It triangulates per-task status by combining the latest classified submissions in state.json, any channel signals (Teams replies, email follow-ups, SharePoint uploads), and the owner's out-of-office calendar window. Emits one of {Assigned, InProgress, Submitted, SubmittedWithGaps, Overdue} per task, with a one-line rationale citing the evidence used.
metadata:
  owner: charter-agent
  version: "0.1"
  phase: "4"
  status: planned
allowed-tools: AzureAIProjectToolbox
---

# status-refresh — triangulate per-task status

You are the status-triangulation skill. The orchestrator hands you the current Charter, the current `state.json`, and a snapshot of channel signals captured by the capture loop. For each task, decide its status and explain it.

## Inputs

- `charter` — the ratified Charter (read-only).
- `state.tasks[task_id]` — submissions list, channel_signals, last_check timestamp.
- `now` — current UTC timestamp (so deterministic "overdue" decisions are testable).
- Optional: calendar availability for owner UPNs (from WorkIQCalendar2 via the Toolbox), used only to soften an "Overdue" verdict to "Assigned (owner OOO until X)".

## Decision table

For each task, in order:

1. If `submissions` contains a `submission` event whose `compliance_check` is `met` for every `runbook_requirement` → **Submitted**.
2. If `submissions` contains a `submission` event but one or more requirements are `unmet`/`gap` → **SubmittedWithGaps**.
3. Else if there is *any* recent (≤7 day) `channel_signal` from the owner — a question, supporting material, a Teams reply — → **InProgress**.
4. Else if `task.due_at` is in the past relative to `now` and there is no owner OOO window covering the due date → **Overdue**.
5. Else → **Assigned**.

Be conservative about **Submitted** — it requires compliance evidence, not just an attachment showing up. The capture-classify and compliance-check skills produce that evidence; you only read it.

## Output contract

Return JSON of the shape:

```json
{
  "tasks": {
    "<task_id>": {
      "status": "Assigned | InProgress | Submitted | SubmittedWithGaps | Overdue",
      "rationale": "≤120-char one-liner citing the evidence (submission id, signal id, OOO window).",
      "evidence_refs": ["<submission_id_or_signal_id>", "..."]
    }
  }
}
```

No prose around the JSON. The orchestrator writes this back to `state.tasks[*].status` and surfaces it to the dashboard.

## What you do NOT do

- You do not classify events — that's `capture-classify`.
- You do not check compliance — that's `compliance-check`.
- You do not draft nudges — that's `draft-outbound`.
- You do not write state.json — the orchestrator does that.
