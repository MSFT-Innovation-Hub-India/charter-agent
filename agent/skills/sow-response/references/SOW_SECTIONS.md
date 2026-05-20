# SOW sections — runbook requirements

What goes into `task.runbook_requirements[]` for each canonical SOW section. These are consumed by the generic `compliance-check` skill when a collaborator submits a section back; bad requirements mean useless compliance reports.

## Rule of thumb

A good `runbook_requirement` is **specific, checkable, and grounded in the RFP**.

- ✅ *"Calls out SLAs for severity 1, 2, and 3 incidents with target response and resolution times."*
- ❌ *"Covers SLAs."* (not checkable — what does "covers" mean?)
- ❌ *"Should be comprehensive."* (not specific to anything in the RFP.)

Always derive requirements by reading the RFP. If the RFP doesn't mention something, do not add it as a requirement — it will only generate noise.

## Canonical sections

### `technical-scope` — Technical scope & solution outlining

Owner: the firm's solution lead named in the kick-off meeting.

Typical requirements (only include the ones the RFP actually demands):

- Maps each functional requirement listed in RFP §<n> to a proposed component or capability.
- Calls out assumed integration points with customer-side systems and their protocols.
- Includes a non-functional requirements summary covering performance, scale, security, availability.
- Identifies dependencies on customer-supplied data, environments, or third-party licences.
- States any in-scope / out-of-scope items explicitly.

### `pm-scope` — Project management scope & accountabilities

Owner: the firm's delivery / PM lead named in the kick-off meeting.

Typical requirements:

- Provides a phase-wise WBS aligned with the technical scope components.
- Names the governance cadence (steering committee, sponsor reviews) and frequency.
- States the RACI for the customer's and the firm's roles for each phase.
- Names assumptions about customer-side resource availability.
- Lists the change-management process for in-flight scope changes.

### `commercial` — Commercial section

Owner: the firm's commercial / pricing lead named in the kick-off meeting.

Typical requirements:

- Effort hours per phase per role aligning with the PM scope WBS.
- Pricing model the RFP requested (T&M, fixed-price, milestone-linked, …).
- Payment milestones tied to deliverables named in the technical and PM scopes.
- Pricing validity period.
- Currency and tax treatment per the RFP's commercial annex.

### `case-studies` — Case studies & customer testimonials

Owner: the SOW Owner (this section is RAG-driven, not human-authored).

Typical requirements:

- At least N case studies relevant to the customer's industry (N per the RFP, usually 2–3).
- Each case study names the customer (or a redacted equivalent if NDA), the problem solved, the firm's role, and the measurable outcome.
- At least one quoted testimonial per case study where available.

The agent fulfils this section by issuing a `WorkIQCopilot___copilot_chat` query of the form:

> *"Find past project case studies and customer testimonials from \<firm-name\> that match the following capabilities and industry: \<distilled list from the RFP\>. Return title, customer, problem, outcome, and any testimonial quotes."*

If a dedicated AI Search WorkIQ server is added to the Toolbox later, this section's `runbook_requirements[]` won't change — only the skill's tool choice will. That's why this rubric lives here, not in code.

## Cross-section consistency

These are the standard `consolidation_rules.cross_section_checks[]` for any SOW Charter. The `consolidate` skill applies them at final-doc time:

1. *"Effort hours quoted in the commercial section match the sum of effort hours in the PM scope WBS."*
2. *"Every component named in the technical scope appears in the PM scope WBS and in the commercial pricing breakdown."*
3. *"Every milestone referenced in the commercial payment schedule corresponds to a deliverable named in the technical or PM scope."*

Add more if the RFP introduces unusual structural constraints (e.g. *"options pricing must reconcile with optional scope items"*).
