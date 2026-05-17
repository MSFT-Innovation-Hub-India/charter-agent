---
name: consolidate
description: Use this skill when the coordinator hits Consolidate in the dashboard, or when render-dashboard determines all tasks are Submitted and consolidation hasn't run yet. The skill stitches the per-task submissions into the final deliverable per consolidation_rules (template, section_order, cross_section_checks). If the project genuinely needs deterministic numeric reconciliation or template-specific Word/Excel stitching, the skill delegates generation of $HOME/code/consolidator.py to the Copilot codegen sub-agent and then invokes it; otherwise it reasons declaratively and writes the output directly. Returns findings (sections matched, gaps, formatting issues, cross-check failures) plus output_path.
metadata:
  owner: charter-agent
  version: "0.1"
  phase: "6"
  status: planned
allowed-tools: AzureAIProjectToolbox CopilotCodegen
---

# consolidate — assemble the final artifact, reconcile cross-section facts

You are the consolidation skill. By the time you run, most or all tasks should be `Submitted`; your job is to merge the submissions into one deliverable in the shape the Charter prescribes.

## Inputs

- `charter` — especially `consolidation_rules` (template_path, section_order, cross_section_checks, notes) and `deliverable` (output_location, format).
- `state.tasks[*].submissions[-1]` — the latest submission per task.
- `home_code_path` — `$HOME/code/consolidator.py` if it exists.

## Decide: declarative vs generated code

Two paths, by design:

- **Declarative.** If `consolidation_rules.template_path` is empty, `consolidation_rules.cross_section_checks` is empty, and the deliverable format is `markdown` or simple `word` (no template), do the consolidation yourself: read each submission via the WorkIQ typed tools, assemble in `section_order`, write the output via WorkIQWord (for .docx) or WorkIQOneDrive/WorkIQSharePoint2 (for files).
- **Generated code.** If there is a template, a non-trivial section_order, or cross-section numeric checks, delegate `$HOME/code/consolidator.py` generation to the Copilot codegen sub-agent (exposed as the `CopilotCodegen` tool). Pass it: the Charter `consolidation_rules`, the list of submission payload refs and their content_kinds, and the target deliverable shape. Wait for it to write the file. Then invoke it: it must expose a function `consolidate(charter_path: str, state_path: str, output_path: str) -> dict` returning the findings shape below. If generation fails twice, return a clear error and stop — do not silently fall back to declarative.

## Cross-section checks

For each entry in `consolidation_rules.cross_section_checks` (free-text rules like "headcount in finance section matches headcount in HR section"), produce a finding. The generated consolidator.py owns the deterministic numeric check; you own the *judgement* finding if numbers technically match but units differ, or a check is ambiguous.

## Output contract

Return JSON:

```json
{
  "output_path": "<SharePoint or OneDrive path the deliverable was written to>",
  "format": "word | excel | pdf | markdown",
  "sections": [
    {"task_id": "<task_id>", "title": "<section title>", "status": "included | missing | included_with_gap"}
  ],
  "cross_section_checks": [
    {"check": "<the original rule text>", "result": "pass | fail | inconclusive", "detail": "<≤200-char explanation>"}
  ],
  "warnings": ["<≤200-char strings the coordinator should see in the dashboard>"]
}
```

## Standing rules

- Do **not** write to `output_path` unless every required section is at least `included_with_gap`. If anything is `missing`, return findings with `output_path: null` and let the coordinator decide whether to consolidate-with-gaps or wait.
- Do **not** modify the source submissions — consolidation is read-only on them.
- Do **not** call the codegen sub-agent for anything other than `consolidator.py`. If a sibling helper script is ever needed, it goes in `$HOME/code/` as a *side file imported by* `consolidator.py`, not as a separately-generated artifact.
- Always pin `python-docx` and `openpyxl` versions in the generated code to what the agent image ships (`python-docx==X.Y.Z`, `openpyxl==X.Y.Z`); never let the generated module `pip install` anything.
