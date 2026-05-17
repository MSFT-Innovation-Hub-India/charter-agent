# test-fixtures/

**Non-normative. Test inputs and reviewer artefacts only.**

Files here are sample data for end-to-end test runs and visual reference — one possible project shape among many. Do **not** anchor any spec, architecture, or implementation decision in the names, owners, sections, statuses, or timelines that appear in these files. The agent must handle arbitrary projects ([AGENTS.md §3 invariant 2](../AGENTS.md) — *generic over specific*).

If a coding agent finds itself reading these files to "understand the design," it has the wrong file open. The design lives in [`functional-specs/`](../functional-specs/) and [`architecture/`](../architecture/).

## Contents

- `sample-meeting-notes.md` — a sample cross-functional kickoff conversation. Use as the input to `propose_charter` during Phase 4+ end-to-end tests.
- `dashboard-mock.html` — single-file HTML/CSS/JS mock showing how the dashboard would render *for the sample above*. Reviewer artefact only; not the implementation. Binding UI requirements live in [`functional-specs/project_workspace_spec.md §5.6`](../functional-specs/project_workspace_spec.md).
- `dashboard-mock-alt.html` — an earlier/alternate mock kept for reference comparison. Same caveats.
- `dashboard-sample.png` — screenshot reference for layout intent. Same caveats.
