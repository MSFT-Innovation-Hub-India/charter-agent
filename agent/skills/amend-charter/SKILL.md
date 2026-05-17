---
name: amend-charter
description: Use this skill when the coordinator wants to amend a ratified Charter â€” typically because a runbook gap was discovered, a stakeholder changed, due dates shifted, or watch_channels need to be added/removed. The skill walks the coordinator through the proposed amendment, validates it doesn't break the Charter's invariants (no orphan dependencies, task owners in stakeholders, project_id immutable), and emits a new Charter object with version incremented. The amendment is itself ratified before it takes effect; nothing else in the system mutates the Charter outside this flow.
metadata:
  owner: charter-agent
  version: "0.1"
  phase: "6"
  status: planned
allowed-tools: workiq
---

# amend-charter â€” propose and validate a Charter amendment

You are the amendment skill. The orchestrator calls you when the coordinator submits an `AmendmentSpec` (or when a `draft-outbound` `propose_amendment` action is approved). Your job is to apply the changes, validate the resulting Charter, and hand it back for ratification.

## Inputs

- `current_charter` â€” the latest ratified Charter (read-only).
- `amendment` â€” an `AmendmentSpec`: `{amendment_id, reason, changes}` where `changes` is a dict of field paths to new values (e.g. `{"tasks.finance-section.due_at": "2026-05-30T17:00:00Z", "stakeholders.owners": ["+legal@example.com"]}`). Lists may use `+`/`-` prefixes on items to add/remove.

## Apply, validate, return

1. Apply the changes to a deep copy of `current_charter`. Keep `project_id` and `version` *exactly* as you found them â€” the orchestrator bumps `version` after re-ratification, not you.
2. Re-validate every Charter invariant: task_ids are unique, `depends_on` references resolve, every `task.owner_upn` is in `stakeholders.{coordinator,deputy,owners}`, deputy and coordinator are distinct, watch_channel kinds are allowed.
3. If the amendment touches `consolidation_rules` materially (`template_path`, `section_order`, `cross_section_checks`), set `consolidator_module_path` back to `null` and add a `warning` so the orchestrator knows to regenerate `$HOME/code/consolidator.py` after re-ratification.
4. Return the new Charter plus a short impact summary the dashboard will show the coordinator before they hit "Ratify amendment".

## Output contract

```json
{
  "new_charter": { "...full Charter JSON...": "" },
  "impact": {
    "fields_changed": ["tasks.finance-section.due_at", "stakeholders.owners"],
    "tasks_affected": ["finance-section"],
    "watchers_added": [],
    "watchers_removed": [],
    "consolidator_needs_regen": false,
    "warnings": ["<â‰¤200-char strings>"]
  }
}
```

Return `{"error": "<reason>"}` if the amendment would violate any invariant; the orchestrator surfaces this back to the coordinator unchanged so they can revise.

## Standing rules

- Never silently drop a `changes` entry. If you can't apply one (path doesn't exist, value invalid), error out with the path that failed.
- Never write `charter.json` yourself â€” the orchestrator's `charter.amend(...)` does the atomic write only after re-ratification.
- Never mutate `current_charter` in place.
