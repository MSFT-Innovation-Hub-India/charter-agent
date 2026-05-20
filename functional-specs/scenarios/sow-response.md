# Scenario: SOW Response

> One scenario of many. The agent stays generic; **all** SOW-specific behaviour lives in the [`sow-response`](../../agent/skills/sow-response/) skill, which **drives the workflow itself** via the host model's tool-loop. This document is the human-readable companion to that skill. **The skill body is the authoritative contract** — when this doc and `SKILL.md` disagree, `SKILL.md` wins.

## 1. Context

An ITES enterprise responds to customer RFPs by compiling a **Statement of Work (SOW)**. The SOW Owner runs an internal kick-off meeting, parcels out sections to internal collaborators (and sometimes external ones), waits for their inputs to arrive over email or Teams, validates each input against the RFP's requirements, and consolidates everything into a final Word document.

The first demo cohort of collaborators is mixed: some are in the same Microsoft Entra tenant as the SOW Owner, some are not. That single fact drives most of the surface-selection logic below.

## 2. Trigger

The SOW Owner opens a new conversation in the dashboard and types something like:

> "I've received an RFP from Contoso. The Teams call with the internal stakeholders happened today. Go and pull the details from it and get started."

The phrase shape "RFP / SOW / Statement of Work / customer proposal" + a reference to a recent meeting or email is what routes the host model to this skill. (Trigger keywords live in the skill's `description` frontmatter; the matching is done by the host `Agent`, not by an `if` in the orchestrator.)

The frontend calls `/invocations` with `{"action": "run_skill", "payload": {"skill_name": "sow-response", "prompt": "<the SOW Owner's prompt>"}}`. The orchestrator frames the prompt with `caller_upn` + `session_id` and hands the skill body to the host `Agent`.

## 3. Architecture in one sentence

**The skill drives.** The host model is given the SKILL.md body as instructions and two tool surfaces — the WorkIQ Toolbox (via the MAF `MCPStreamableHTTPTool`) and a small set of agent-side state primitives ([`runtime/state_tools.py`](../../agent/src/charter_agent/runtime/state_tools.py)) — and runs the workflow turn-by-turn through MAF's tool-loop. There is no per-scenario Python.

## 4. What the **skill** is responsible for (domain layer)

Everything in this section is defined in [`SKILL.md`](../../agent/skills/sow-response/SKILL.md). The bullets below are a tour, not a contract.

1. **Idempotency check.** Read `project_log.json` from `$HOME` if it exists; skip steps already recorded.
2. **Grounding.** One `WorkIQCopilot___copilot_chat` call for open-ended discovery, then drill into the cited Teams meeting (`WorkIQTeams___…` / `WorkIQCalendar2___…`) and the RFP file (`WorkIQMail2___…` / `WorkIQSharePoint2___…` / `WorkIQOneDrive___…`). Resolve UPNs and `is_external` with `WorkIQUser___…`.
3. **Communication matrix.** Per [`references/COMMUNICATION_MATRIX.md`](../../agent/skills/sow-response/references/COMMUNICATION_MATRIX.md): internal → Teams DM preferred, document sharing over OneDrive/SharePoint/email/Teams; external → email only, document sharing over email only.
4. **Task synthesis.** One task per SOW section (minimum: `technical-scope`, `pm-scope`, `commercial`, `case-studies`). Per-task `runbook_requirements` extracted from the RFP — checkable bullets, not paraphrases. See [`references/SOW_SECTIONS.md`](../../agent/skills/sow-response/references/SOW_SECTIONS.md).
5. **Persist.** Two files via `state_write_text` / `state_write_json`:
   - `project_charter.md` — human-readable charter (template in SKILL.md §5a).
   - `project_log.json` — structured workflow state (schema in SKILL.md §5b).
6. **Kickoff fan-out.** For each task, send the kickoff message on the owner's `communication_modes.preferred` channel — `WorkIQTeams___SendMessageToUser` for internals, `WorkIQMail2___SendEmailWithAttachments` for externals — then read-modify-write `project_log.json` to record `kickoff_sent` and append a `log_entries[]` row. After every step, call `log_workflow_step(kind, summary, ref)` to append to `$HOME/activity.json`.
7. **Closing receipt.** ≤4 sentences back to the SOW Owner; no JSON dumps.
8. **Consolidation (later phase).** Will use the generic `consolidate` skill against `project_log.json`'s `consolidation_rules`; not part of the kickoff path.

## 5. What the **agent code** is responsible for (generic plumbing)

| Concern | Lives in | Status |
|---|---|---|
| `/invocations` dispatch (`echo`, `list_tools`, `run_skill`), session resume, prompt framing with `caller_upn` + `session_id` | [`orchestrator.py`](../../agent/src/charter_agent/orchestrator.py), [`runtime/foundry_host.py`](../../agent/src/charter_agent/runtime/foundry_host.py) | done |
| Skill discovery + frontmatter parsing | [`runtime/skill_loader.py`](../../agent/src/charter_agent/runtime/skill_loader.py) | done |
| State primitives exposed as MAF agent-side tools (`state_write_text`, `state_read_text`, `state_write_json`, `state_read_json`, `state_list_files`, `state_file_exists`, `log_workflow_step`) | [`runtime/state_tools.py`](../../agent/src/charter_agent/runtime/state_tools.py) | done |
| Path-validated, atomic `$HOME` I/O (rejects `..`, absolute paths) | [`state.py`](../../agent/src/charter_agent/state.py) | done |
| Activity log (NDJSON append to `$HOME/activity.json`) | [`observability.py`](../../agent/src/charter_agent/observability.py) | done |
| WorkIQ Toolbox attached as `MCPStreamableHTTPTool` with the `Foundry-Features` + `Authorization` headers stamped per-request | [`runtime/foundry_host.py`](../../agent/src/charter_agent/runtime/foundry_host.py) | done |
| Coordinator-OBO token acquisition with deputy fallback | `runtime/workiq_token.py` | **not built yet** — dev uses `DefaultAzureCredential` from `az login` |
| Channel polling, "since" cursors, dedup | `capture/handlers/*` | **Phase 4 — not built yet** |
| `SuggestedAction` draft → coordinator-approve → idempotent execute | `actions/` | **Phase 5 — not built yet** |
| Final Word doc stitching | `$HOME/code/consolidator.py`, generated by the codegen sub-agent | **Phase 6 — not built yet** |

No SOW-specific code exists or is planned. The `charter/` and `kickoff/` Python packages were removed when this skill became self-driving; their responsibilities now live entirely inside SKILL.md.

## 6. State this scenario writes to `$HOME`

| File | Owner | Shape |
|---|---|---|
| `project_charter.md` | skill | Markdown — template in [SKILL.md §5a](../../agent/skills/sow-response/SKILL.md) |
| `project_log.json` | skill | JSON — schema in [SKILL.md §5b](../../agent/skills/sow-response/SKILL.md) |
| `activity.json` | agent code (via `log_workflow_step`) | NDJSON, one object per line: `{at, actor, kind, summary, ref, span_id}` |
| `state.json` | agent code (echo verb only) | Counter used by the Phase 1 smoke test |

There is no Pydantic schema for `project_log.json`. The SKILL.md body is the schema — per [AGENTS.md §4.4](../../AGENTS.md) "Skill prompts as the schema contract". Future scenarios are free to define a completely different log shape; the agent code never inspects it.

## 7. Routing

Today the BFF picks `skill_name` from the URL / context. When a second scenario lands, replace that with: ask the host `Agent` to pick the skill by description match given the SOW Owner's prompt, fall back to a configured default. Single-verb refactor inside `orchestrator._run_skill`, no architecture change.

## 8. Out of scope

- AI Search RAG wiring for case-studies retrieval. The skill calls `WorkIQCopilot___copilot_chat` with an RFP-grounded query; if a dedicated AI Search WorkIQ server appears, the skill switches to it without code change.
- A third runtime (e.g. a separate Anthropic API key for SOW drafting) — invariant 12.
- Cross-RFP analytics. One project = one `project_log.json`; aggregation across SOWs is not a project of this agent.
- Document sharing with external collaborators over Teams or SharePoint. Hard rule from the communication matrix; the skill enforces it.
