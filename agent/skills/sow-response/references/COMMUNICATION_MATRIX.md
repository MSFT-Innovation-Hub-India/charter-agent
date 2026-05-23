# Drafting kickoffs and nudges — tone

Channel and audience choices are handled by `add_charter_task` (sets each task's `communication_modes` from the `is_external` flag). This file is just the voice-and-shape guidance for the messages themselves.

## Internal nudge over Teams

Conversational, one sentence, no signature.

> *"Hey — just checking on the technical scope section, due EOD today. Anything blocking?"*

## Internal nudge over email

Two short paragraphs. Polite, name the section, name the deadline, offer help.

> *"Hi — touching base on the technical scope section for the Contoso SOW (due EOD today). Happy to jump on a quick call if anything's blocked. Thanks!"*

## External nudge over email

Three short paragraphs, more formal. Restate the RFP context, name the section, name the deadline, sign off with the SOW Owner's full name and title.

Default subject: *"&lt;Customer&gt; SOW — &lt;section title&gt; — quick follow-up"*.

## Rules that apply to every channel

- Never CC anyone the SOW Owner did not explicitly add.
- Never escalate by looping in the collaborator's manager without explicit approval from the SOW Owner.
- Never invent a deadline. Use the task's `due_at` verbatim (the project tools store it in ISO; render it as a short human form, e.g. "EOD Fri 30 May").
- Kickoff messages follow the same tone, plus the runbook bullets the collaborator needs to address — one short HTML `<ul>` is enough.
