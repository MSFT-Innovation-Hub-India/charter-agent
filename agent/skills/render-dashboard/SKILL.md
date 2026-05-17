---
name: render-dashboard
description: Use this skill when the BFF calls /invocations with render_dashboard. It assembles the SPA payload — Charter, per-task status (from status-refresh), latest submissions, exceptions, pending SuggestedActions, activity-log tail — and filters it by the visitor's role (coordinator sees all, owner sees their tasks plus shared context, observer sees a summary). The skill does NOT recompute status or draft actions; those skills run first as part of the same invocation cycle and the orchestrator passes their outputs in.
metadata:
  owner: charter-agent
  version: "0.1"
  phase: "5"
  status: planned
allowed-tools: AzureAIProjectToolbox
---

# render-dashboard — assemble and role-filter the SPA payload

You are the renderer. Your output is a JSON payload the SPA renders directly. Determinism matters here — the same inputs must produce the same output, byte-for-byte, so the SPA can diff intelligently.

## Inputs

- `charter` — the ratified Charter (or proposed Charter, if not yet ratified).
- `state` — the full `state.json`.
- `triangulated_status` — the latest output from `status-refresh` (already merged into `state.tasks[*].status` by the orchestrator).
- `suggested_actions` — pending `SuggestedAction[]` from `draft-outbound`.
- `activity_tail` — the last N entries of `activity.json` (the orchestrator chooses N).
- `visitor` — `{upn, role: coordinator|owner|observer}`. Role is derived by the orchestrator from the visitor's UPN against the Charter stakeholders.

## Role filtering

- **coordinator**: sees everything — every task, every submission, every signal, every pending action, the full activity tail, the close button.
- **owner**: sees only the tasks where `task.owner_upn == visitor.upn`, plus the Charter header (so they have context), plus their own pending actions (where `target.upn == visitor.upn`), plus a redacted activity tail (only entries touching their tasks). No close button, no other owners' submissions.
- **observer**: sees the Charter header, the task list with `{title, owner_upn, status, due_at}` only (no submission content, no signals, no actions), and the project's overall percentage-complete. No activity tail.

## Output contract

```json
{
  "charter": { "...filtered fields..." : "" },
  "tasks": [
    {
      "task_id": "<task_id>",
      "title": "...",
      "owner_upn": "...",
      "status": "Assigned | InProgress | Submitted | SubmittedWithGaps | Overdue",
      "due_at": "...",
      "last_signal_at": "...",
      "rationale": "<from status-refresh, only for coordinator/owner>"
    }
  ],
  "exceptions": [ { "kind": "...", "detail": "..." } ],
  "pending_actions": [ /* SuggestedAction shape, only the ones visible to this role */ ],
  "activity": [ /* tail, role-filtered */ ],
  "viewer": {"upn": "...", "role": "coordinator|owner|observer"},
  "is_closed": false
}
```

## Standing rules

- Do **not** call any WorkIQ tool. All data needed for the render is already in the inputs.
- Do **not** mutate state, draft actions, or compute status — those are other skills' jobs.
- Strip any field an observer or owner shouldn't see *before* returning; do not rely on the SPA to redact.
- Sort tasks by Charter declaration order, not by status — the coordinator's mental map of the project depends on a stable order.
