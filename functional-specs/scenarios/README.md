# Scenario specs

The agent itself is **generic** (see [AGENTS.md §3 invariant 2](../../AGENTS.md)). Every project-shape-specific behaviour — the playbook for "how a SOW gets written", "how an audit gets run", "how a board pack gets compiled" — lives in **one Agent Skill** under [`agent/skills/`](../../agent/skills/), and its companion **scenario spec** lives in this folder.

A scenario spec is the human-readable "what & why" for one skill. It is paired one-to-one with the skill folder:

| Scenario spec | Skill folder | Status |
|---|---|---|
| [`sow-response.md`](sow-response.md) | [`agent/skills/sow-response/`](../../agent/skills/sow-response/) | drafted |
| _(future)_ board-pack | _(future)_ `agent/skills/board-pack/` | — |
| _(future)_ audit-response | _(future)_ `agent/skills/audit-response/` | — |

## Authoring rules

1. **Domain knowledge → skill, not code.** Run every new behaviour through the [§4.4 decision rule](../../AGENTS.md). If you find yourself wanting a `sow/` Python module, you're doing it wrong.
2. **The spec separates four concerns explicitly** — trigger, skill-layer (domain reasoning), agent-layer (already-generic plumbing it relies on), and the Charter shape this scenario produces. Don't mix them.
3. **Reference the generic spec, don't duplicate it.** All the Charter/state/action lifecycle is in [`../project_workspace_spec.md`](../project_workspace_spec.md). A scenario spec only documents what's *different* or *specific* about that scenario.
4. **One skill per scenario.** Sub-tasks (e.g. "compliance-check a received document") use the existing generic skills (`compliance-check`, `draft-outbound`, `consolidate`); they are not re-implemented per scenario.
