# Communication matrix — SOW Response

How to decide each collaborator's `communication_modes` entry in the Charter.

The single signal you need: **is the collaborator's email domain the same as the SOW Owner's, or different?**

## Same domain (internal collaborator)

Same Microsoft Entra tenant. All M365 surfaces are available.

```jsonc
{
  "preferred": "teams_message",
  "allowed": ["teams_message", "email"],
  "document_sharing": ["onedrive", "sharepoint", "email", "teams_message"],
  "is_external": false
}
```

Why `teams_message` preferred: internal collaborators respond faster to a 1:1 Teams ping than to email. The SOW Owner can override per-collaborator at ratification.

## Different domain (external collaborator)

External partner / customer SME / contractor. **Cross-tenant Teams and SharePoint sharing are not available for the demo cohort.** Email is the only viable surface for both messaging and document sharing.

```jsonc
{
  "preferred": "email",
  "allowed": ["email"],
  "document_sharing": ["email"],
  "is_external": true
}
```

If a future cohort enables cross-tenant collaboration via guest invites or B2B sharing, this file gets updated — no code change required.

## Tone guidance for nudges

When the SOW Owner approves a `nudge_owner` `SuggestedAction`, the `draft-outbound` skill writes the body. For SOW specifically:

- **Internal nudge over Teams.** Conversational, single sentence, no signature: *"Hey — just checking on the technical scope section, due EOD today. Anything blocking?"*
- **Internal nudge over email.** Two short paragraphs, polite, name the section, name the deadline, offer help: *"Hi — touching base on the technical scope section for the Contoso SOW (due EOD today). Happy to jump on a quick call if anything's blocked. Thanks!"*
- **External nudge over email.** Three short paragraphs, more formal, restate the RFP context, name the section, name the deadline, sign off with the SOW Owner's full name and title. Default subject: *"Contoso SOW — <section title> — quick follow-up"*.

Never CC anyone the SOW Owner did not explicitly add. Never escalate by adding the collaborator's manager without an explicit approved `SuggestedAction` of kind `propose_reassign` or `clarify_gap`.
