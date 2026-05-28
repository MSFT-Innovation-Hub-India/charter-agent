---
name: sow-kickoff-extract
description: >
  SOW workflow step 3 — find the kickoff meeting notes and extract task
  assignments. Searches Teams then email for the post-RFP kickoff meeting.
  Creates one task per commitment made by team members in the notes.
  Activated after the charter is drafted.
metadata:
  owner: charter-agent
  workflow: sow-response
  phase: charter_drafted
allowed-tools: >
  project_read_log project_patch_log log_workflow_step
  WorkIQMail2___* WorkIQTeams___* WorkIQUser___*
---

# SOW Step 3 — Extract Kickoff Meeting Notes

Your only job this turn: find the kickoff meeting notes, extract every commitment a team member made, and assign tasks directly from those commitments. Show the raw content you find before interpreting it.

## 1. Read the project log

Call `project_read_log()`. You need `customer_name`, `project_name`, and the SOW Owner's email domain.

## 2. Find the meeting notes

Try in order, stop when you find them:

**Step A — Teams first:** Call `log_workflow_step("searching_teams", "Searching Teams for <customer_name> kickoff meeting notes")`. Then ask `WorkIQTeams___*` to find messages or threads about the `<customer_name>` kickoff meeting for `<project_name>` — meeting notes, agendas, task assignments, or follow-ups from people on your team. Request the top 3 results only. If a match is found, read the full thread before interpreting.

**Step B — Mail fallback (if Teams finds nothing):** Call `log_workflow_step("searching_email_fallback", "Teams found nothing — searching email for <customer_name> kickoff notes")`. Then ask `WorkIQMail2___*` to search for emails about the `<customer_name>` kickoff meeting, meeting notes, or SOW follow-ups. Request the top 3 results only. Read full bodies and fetch attachments.

If nothing found: patch `{ "phase": "charter_drafted" }` (stay here for retry), call `log_workflow_step("kickoff_search_failed", "Kickoff notes not found")`, tell the user what you searched and ask them to share the notes. Stop.

## 3. Show the raw content you found

Before interpreting or assigning, show the SOW Owner what you actually retrieved:

```
## Source: <email subject / meeting title / Teams thread>
**Date**: <date>  **From/In**: <sender or participants>

<Paste the relevant portion of the notes verbatim, or a faithful near-verbatim excerpt>
```

This is important — the SOW Owner needs to verify you found the right notes.

## 4. The two-source rule — critical

**The RFP names the customer's people. The kickoff names your team's people. Never mix them.**

- Task owners come **only** from the kickoff notes / meeting attendees — people on your team
- Task titles and scope come **only** from what each person committed to in the kickoff notes — do not use charter section titles or RFP text as task titles or requirements
- Never assign a task to a person named in the RFP — those are customer contacts
- Copy email addresses verbatim from the source; do not look up an address you already have

**Internal vs external:** A collaborator is internal if their email domain matches the SOW Owner's domain. Everyone else is external (email-only channel).

**Directory ID for internal owners:** For each internal task, call `WorkIQUser___*` to look up the `owner_upn` and extract their directory identity ID as `owner_oid`. Skip for external owners.

## 5. Assign tasks — one per commitment in the notes

**Primary rule:** create one task for each distinct commitment a team member made in the kickoff notes. Use the task title and scope verbatim from the notes — what the person said they would deliver. Do **not** derive tasks from charter sections; the notes and the charter structure may differ in count, grouping, and title.

If a commitment naturally corresponds to a charter section, record the matching `section_id`; otherwise use `"additional-<n>"`. The `section_id` is a cross-reference hint, not the source of truth for what to do.

Do not limit tasks to the number of charter sections. Every distinct commitment in the notes becomes one task.

If the notes name someone with no email address: ask the SOW Owner before assigning.

## 6. Show the task table and patch the log

Show:

```
## Kickoff Summary
**Source**: <meeting title / email subject>  **Date**: <date>
**Participants**: <names and roles>

## Task Assignments
| Section | Owner | Due | Channel |
|---|---|---|---|
| <title> | <name> (<upn>) | <date or TBD> | Teams / Email |
```

Flag any commitment that has no identified owner.

Patch — only the keys this step owns:

```
project_patch_log({
  "phase": "kickoff_found",
  "kickoff": {
    "found": true,
    "source_ref": "<ref>",
    "date": "<ISO date>",
    "attendees": ["<name (upn)>"]
  },
  "tasks": [
    {
      "id": "task-<n>",
      "section_id": "<charter section id or 'additional-<n>' for extra commitments>",
      "title": "<section title or commitment description>",
      "requirements": ["<verbatim from meeting notes — what the person committed to deliver>"],
      "owner_display_name": "<name from meeting notes>",
      "owner_upn": "<email verbatim from source>",
      "owner_oid": "<directory identity ID or null>",
      "is_external": "<true if external, false if internal>",
      "due_at": "<ISO date or null>",
      "kickoff_sent": false,
      "kickoff_channel": null,
      "last_polled_at": null,
      "status": "pending",
      "submissions": []
    }
  ]
})
```

Call `log_workflow_step("kickoff_found", "Kickoff notes found: <N> tasks assigned", "<source_ref>")`.

Tell the user how many tasks were assigned. Do not ask for confirmation — the orchestrator will proceed immediately to send kickoff messages.
