---
name: sow-rfp-search
description: >
  SOW workflow step 1 — find the RFP. Searches the user's inbox and attachments
  for the RFP email matching the project in scope. Activated automatically on a
  new SOW project and on every retry while the RFP has not yet been located.
metadata:
  owner: charter-agent
  workflow: sow-response
  phase: searching_rfp
allowed-tools: >
  project_read_log project_write_log project_patch_log log_workflow_step
  WorkIQMail2___* dashboard_payload publish_view
---

# SOW Step 1 — Find the RFP

Your only job this turn: locate the RFP for this project and show its full detail to the SOW Owner.

## 1. Read the project log

Call `project_read_log()`. It returns the full log or an empty dict for a brand-new project.

**If the log is empty (new project):** extract `customer_name` from the user's message.
Use whatever the user provides — a company name, a person's name, a project codename, a product name. Do NOT ask for a `project_name` separately. The engagement name will be taken from the RFP itself once found.
If no customer or engagement name can be inferred from the user's message at all, then ask for one — but ask only for the organisation or engagement name, not a "project name".

Then call `project_write_log(...)` with:

```json
{
  "skill": "sow-response",
  "phase": "searching_rfp",
  "customer_name": "<extracted>",
  "project_name": "",
  "rfp": { "found": false },
  "charter": {},
  "kickoff": { "found": false },
  "tasks": [],
  "log_entries": []
}
```

**If the log exists:** use `customer_name` from it.

## 2. Search for the RFP

Ask `WorkIQMail2___*` to search for emails from or about `<customer_name>` mentioning an RFP, proposal, scope of work, tender, or bid. Request the top 3 results only. For each relevant result, read the full body. If a message has attachments, fetch those too.

## 3a. RFP found — show FULL content and advance

Show the SOW Owner the complete details of what you found. Do not summarise away information:

```
## RFP Found: <subject line>

**From**: <sender>  **Received**: <date>
**Customer**: <customer name>
**Engagement / Project name**: <how the RFP refers to the engagement — use this as project_name>
**Submission deadline**: <date and time, if stated>

### What the RFP asks for

For each section requested, show the title and every requirement bullet verbatim or near-verbatim:

**<Section title>**
- <requirement as stated in the RFP>
- <requirement as stated in the RFP>
```

If the RFP body is very long, show all sections but summarise requirement bullets to 1–2 lines each.

Patch the log — only the keys this step owns:

```
project_patch_log({
  "phase": "rfp_found",
  "project_name": "<name the RFP uses for the engagement>",
  "rfp": {
    "found": true,
    "source_ref": "<message_id>",
    "subject": "<subject>",
    "received_at": "<ISO date>",
    "summary": "<2-sentence summary>",
    "sections": [
      { "title": "<section title>", "requirements": ["<requirement verbatim>"] }
    ]
  }
})
```

Include **every section the RFP mentions**, even if it seems minor. Call `log_workflow_step("rfp_found", "RFP located: <subject>", "<message_id>")`.

Call `dashboard_payload()` and pass the result to `publish_view(payload)`.

## 3b. RFP not found — halt and ask

Tell the user exactly what you searched and ask them to forward the RFP email.

Patch: `project_patch_log({ "phase": "searching_rfp" })`. Call `log_workflow_step("rfp_search_failed", "RFP not found — awaiting forwarded email")`.

Do not invent RFP content. Do not advance the phase.

## Plain questions

If the user asks about the project state, answer from the log. Do not re-run the search or change the phase.
