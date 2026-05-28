---
name: sow-task-allocate
description: >
  SOW workflow step 4 — send kickoff messages to task owners. Teams DM for
  internal owners, email for external. Falls back to email if Teams fails.
  Activated after the SOW Owner confirms the task assignments from step 3.
metadata:
  owner: charter-agent
  workflow: sow-response
  phase: kickoff_found
allowed-tools: >
  project_read_log project_patch_log log_workflow_step
  WorkIQMail2___* WorkIQTeams___*
---

# SOW Step 4 — Send Kickoff Messages

Your only job this turn: send one kickoff message per task owner and record every outcome.

## 1. Read the project log

Call `project_read_log()`. Work through `tasks[]` where `kickoff_sent: false`.

## 2. Send one message per owner

Process all tasks. A single failure does not stop the others.

### Internal owners (`is_external: false`) — Teams DM first

Short and conversational. Cover: the section they own, the due date, and what they agreed to deliver. No formal sign-off.

If Teams DM fails: send email to `owner_upn` instead. Record `kickoff_channel: "email (teams failed)"`. Do not attempt Teams again for this owner in future turns.

### External owners (`is_external: true`) — email only

Three short paragraphs, formal tone. Para 1: RFP context. Para 2: section title, deadline, requirement bullets. Para 3: sign-off with the SOW Owner's name.

Subject: `"<customer_name> SOW — <section title> — your input needed"`

## 3. Update each task and patch the log

After all sends, build the updated `tasks[]` array (copy the existing array from the log, update each task's fields):

```json
{
  "kickoff_sent": true,
  "kickoff_channel": "teams | email | email (teams failed)",
  "kickoff_sent_at": "<ISO timestamp>"
}
```

Patch in a single call:

```
project_patch_log({
  "phase": "tasks_allocated",
  "tasks": [ <full updated tasks array> ]
})
```

## 4. Show the receipt

```
## Kickoff Messages Sent

| Owner | Section | Channel | Status |
|---|---|---|---|
| <name> | <title> | Teams / Email | ✓ Sent / ✗ Failed |
```

For any failures: explain the error and note that email will be used for this owner going forward.

Call `log_workflow_step("kickoff_sent", "Kickoff messages sent: <N> succeeded, <M> failed")`.

Tell the user: "All kickoff messages dispatched. Trigger me again whenever you want a status update — I'll check for replies."
