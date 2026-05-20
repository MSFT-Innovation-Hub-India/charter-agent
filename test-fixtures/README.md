# test-fixtures/

**Non-normative. Test inputs and reviewer artefacts only.**

Files here are sample data for end-to-end test runs and visual reference — one possible project shape among many. Do **not** anchor any spec, architecture, or implementation decision in the names, owners, sections, statuses, or timelines that appear in these files. The agent must handle arbitrary projects ([AGENTS.md §3 invariant 2](../AGENTS.md) — *generic over specific*).

If a coding agent finds itself reading these files to "understand the design," it has the wrong file open. The design lives in [`functional-specs/`](../functional-specs/) and [`architecture/`](../architecture/).

## Contents

### Current SOW-response demo fixtures

- `sample-meeting-notes.md` — sample kickoff meeting notes for the Northwind Trading Corp SOW response scenario. Use as the grounding input to the `sow-response` skill during demos and Phase 4+ end-to-end tests. Paired one-to-one with `northwind-rfp.md`.
- `northwind-rfp.md` — sample RFP document from Northwind Trading Corp (reference `NWT-RFP-2026-LOG-014`). Email this to yourself with the file attached to trigger the demo end-to-end via the WorkIQ mail capture path.

### Retired-example dashboard mocks (kept for visual reference only)

The three files below depict a **retired example scenario** — *Project Lumen, a customer escalation recovery proposal*. They predate the current SOW-response demo fixtures and do **not** render what the dashboard would show for the Northwind SOW. They are kept solely as visual reference for layout intent. Binding UI requirements live in [`functional-specs/project_workspace_spec.md §5.6`](../functional-specs/project_workspace_spec.md), not here.

- `dashboard-mock.html` — single-file HTML/CSS/JS mock of the Project Lumen scenario. Reviewer artefact only.
- `dashboard-mock-alt.html` — earlier/alternate mock of the same retired scenario.
- `dashboard-sample.png` — screenshot reference for layout intent.
