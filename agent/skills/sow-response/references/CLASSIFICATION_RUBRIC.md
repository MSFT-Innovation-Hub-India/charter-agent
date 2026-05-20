# Classification rubric — capture loop (§9 of SKILL.md)

Loaded on demand when classifying a candidate reply against a task's `runbook_requirements`. Keep the judgement narrow: this rubric is **not** a content review of the response — it's a gate that decides what `tasks[].status` should become and what to surface in the SOW Owner's digest.

## Inputs

- **Candidate message**: the new Mail/Teams reply (subject, body, any attachments WorkIQ surfaced).
- **Task context**: `tasks[].title`, `tasks[].runbook_requirements` (the bulleted asks the kickoff sent out), `tasks[].submissions[]` already on file (so you can tell if this is a revised submission).
- **Owner**: the person you kicked off; the message must be from them or someone they delegated to in-thread (a CC/forward from the owner counts; an unrelated third party doesn't).

## Output (per message)

```json
{
  "accepted": true,
  "covered": ["<requirement bullet that IS addressed>", "..."],
  "gaps":    ["<requirement bullet that is NOT addressed>", "..."],
  "summary": "<one factual sentence; no praise, no hedging>"
}
```

## Decision rules

1. **Is the reply a submission at all?**
   - Yes → it contains content responding to the kickoff's asks (a draft section, a proposal, a pricing table, a case study, a Word/PDF attachment with the deliverable, etc.). Continue to step 2.
   - No, it's a **clarifying question** from the owner ("can you confirm the deadline?", "is this Azure-only or also AWS?") → set `accepted: false`, `covered: []`, `gaps: <all requirements>`, `summary` quotes the question verbatim (≤25 words). The §8 digest will surface this as a recommended action for the SOW Owner to answer.
   - No, it's a **status update with no content** ("I'm working on it, will send Friday") → set `accepted: false`, `covered: []`, `gaps: <all requirements>`, `summary` paraphrases the ETA. Do not mark as submitted.
   - No, it's **off-topic** (auto-reply, OOO, signature-only, marketing) → return `null` from the classifier; the caller should skip appending a submission.

2. **Coverage check** — for each bullet in `runbook_requirements`, decide independently:
   - **Covered** if the reply addresses the bullet substantively. "Substantively" means a reader could act on it without follow-up. Mentioning the topic without giving an answer is **not** covered ("we'll discuss pricing later" does not cover a *pricing* requirement).
   - **Gap** otherwise.
   - When in doubt, mark it a gap. A false gap costs the SOW Owner one clarifying email; a false coverage costs them a missed requirement in the final deliverable.

3. **Acceptance** — `accepted = true` if at least one requirement is covered AND the reply contains submission content (step 1 = yes). `accepted = false` if step 1 was a clarifying question / status update / off-topic.

4. **Revised submission detection** — if `tasks[].submissions[]` already has an `accepted: true` entry from the same owner, this message is a revised submission. Keep the same shape; the latest accepted submission "wins" for the §8 digest, but do not delete earlier entries (the audit trail matters).

## Edge cases

- **Forwarded content**: if the owner forwards a reply from someone else (e.g. a subcontractor's pricing), treat it as a submission from the owner — they vouched for it by forwarding.
- **Multi-task reply**: one message that covers multiple tasks (common when the same person owns two related tasks). Run classification once per task; the same `source_ref` will appear in multiple `tasks[].submissions[]` entries — that's expected.
- **Attachment-only reply** ("see attached"): if the WorkIQ surface doesn't give you the attachment body, mark `accepted: true` with `summary: "submission attached: <filename> (content not inspected)"` and put every requirement bullet in `gaps` — the SOW Owner will need to open the attachment. Better to flag for review than to silently mark the task done.
- **Multiple replies arrived since `last_polled_at`**: classify each independently in chronological order. The latest accepted one drives `tasks[].status`; earlier ones still get appended to `submissions[]`.

## What this rubric is NOT

- It is **not** a quality review. Don't downgrade `accepted` because the writing is weak or the answer is short — that's the SOW Owner's call when they read the digest.
- It is **not** a compliance review against the customer's RFP language. The `runbook_requirements` were already distilled from the RFP at kickoff; you classify against those bullets, not the original RFP.
- It is **not** a scoring system. There is no numeric confidence — booleans + bullets only. The digest is meant to be skim-readable.
