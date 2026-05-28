---
name: sow-reply-poll
description: >
  SOW workflow step 5 — poll for task owner replies and show a status digest.
  Checks email and Teams since each owner's last_polled_at, classifies replies
  against requirements, and proposes (never sends) follow-ups. Repeats on every
  trigger until all tasks are complete.
metadata:
  owner: charter-agent
  workflow: sow-response
  phase: tasks_allocated
allowed-tools: >
  project_read_log project_patch_log log_workflow_step
  WorkIQMail2___* WorkIQTeams___*
  dashboard_payload publish_view
---

# SOW Step 5 — Poll for Replies

Your only job this turn: check for new replies from each owner, classify them, and show the SOW Owner a digest. Never send a follow-up in the same turn you propose it.

## 1. Read the project log

Call `project_read_log()`. Work through `tasks[]` where `kickoff_sent: true`.

## 2. Poll all owners concurrently

Fan all polls at once — do not poll serially.

**Internal owners:** Check their Teams DM thread since `last_polled_at`, then email from the same owner since `last_polled_at`. Check both surfaces every turn.

**External owners:** Email only since `last_polled_at`.

**Skipped:** Tasks where `kickoff_sent: false` — surface in digest as "kickoff not yet sent."

Fetch any shared files or attachments before classifying.

## 3. Classify each reply

**Is it a submission?** The reply must contain content responding to the section requirements.

- **Clarifying question** ("Can you confirm the deadline?"): note the question, surface as recommended action. Status unchanged.
- **Status update with no content** ("sending Friday"): note the ETA. Status unchanged.
- **Off-topic / OOO / auto-reply**: skip entirely.

**Coverage check:** For each requirement bullet, assess independently. "Mentioning the topic" is not coverage — the reader must be able to act on it without follow-up. When in doubt, mark it a gap.

**Set status:**
- All bullets covered → `"submitted"`
- At least one covered, at least one gap → `"submitted_with_gaps"`
- No coverage (question / status update) → status unchanged

**Append to `task.submissions[]`:**
```json
{
  "source_ref": "<message_id or Teams ref>",
  "received_at": "<ISO timestamp>",
  "covered": ["<covered bullet>"],
  "gaps": ["<gap bullet>"],
  "summary": "<one factual sentence — no praise>"
}
```

## 4. Patch the log

Read the current `tasks[]`, apply the updates, and patch in one call:

```
project_patch_log({
  "tasks": [ <full updated tasks array with new submissions and last_polled_at> ]
})
```

Set `last_polled_at` to now for every task that was polled, regardless of whether a reply was found.

## 5. Show the digest

One sentence on overall state and time since kickoff. Then one line per task showing: section title, owner name, status, and a brief factual note on the latest signal or gap. Then **Recommended actions** — one per task that needs attention: who, why, channel, proposed message draft.

End with: "Say 'send them all' or name specific owners to send any of the above. I won't send anything until you confirm."

Call `log_workflow_step("polled", "Polled <N> owners; <M> new replies found")`.

Then emit the dashboard: call `dashboard_payload()` and immediately pass the result to `publish_view(payload)`.

Stay in `phase: "tasks_allocated"` — this step repeats on every trigger. Do not change the phase.
