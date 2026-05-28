---
name: sow-charter-draft
description: >
  SOW workflow step 2 — draft the charter skeleton. Reads RFP sections from the
  project log and produces structured per-section requirement lists. Activated
  automatically after the RFP has been located.
metadata:
  owner: charter-agent
  workflow: sow-response
  phase: rfp_found
allowed-tools: >
  project_read_log project_patch_log log_workflow_step
  dashboard_payload publish_view
---

# SOW Step 2 — Draft the Charter

Your only job this turn: turn every RFP section into a structured charter skeleton with testable requirements.

## 1. Read the project log

Call `project_read_log()`. Source material is `rfp.sections[]`.

**Critical**: include EVERY section from `rfp.sections[]`. Do not drop sections that seem minor, vague, or administrative. The SOW Owner needs a charter entry for everything the RFP asks for — gaps at this stage create missed deliverables later.

## 2. Refine requirements into testable bullets

For each section in `rfp.sections`, refine the requirements so a reviewer can assess a response definitively — each bullet must be specific and verifiable, not a vague prompt. Keep every bullet the RFP asked for. Do not merge sections. Do not add sections the RFP did not request. Do not assign owners — owners come from the kickoff meeting notes in the next step.

## 3. Show the charter and patch the log

Show the SOW Owner every section:

```
## Charter Sections (<N> total)

**<Section Title>**
- <testable requirement>
- <testable requirement>

(repeat for every section)
```

Patch — only the keys this step owns:

```
project_patch_log({
  "phase": "charter_drafted",
  "charter": {
    "sections": [
      {
        "id": "<kebab-case-id>",
        "title": "<section title>",
        "requirements": ["<testable requirement>"]
      }
    ]
  }
})
```

The `sections` array must contain one entry per `rfp.sections[]` entry — same count, same order.

Call `log_workflow_step("charter_drafted", "Charter skeleton built: <N> sections")`.

Call `dashboard_payload()` and pass the result to `publish_view(payload)`.

Tell the user how many sections were drafted and that the next step will search for kickoff meeting notes.
