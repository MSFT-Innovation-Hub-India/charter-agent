# Project Workspace — Implementation Specification

> An agent-orchestrated project coordination workspace built on Microsoft Foundry hosted agents and WorkIQ. The agent is **generic** — its behaviour is shaped by Agent Skills loaded at boot. Today the only scenario in scope is the **SOW Response** workflow, packaged as the [`sow-response`](../agent/skills/sow-response/) skill. Additional scenarios (board pack, audit response, etc.) would land as additional skills with no change to the agent itself.

---

## 1. Purpose of this document

This spec is the implementation contract for the system end-to-end. It captures the use case (§2), the architecture (§3), the technology choices and why each was made (§4), the user experience (§5), the generic project model (§6), the per-scenario customisation pattern (§7), the on-visit refresh loop (§8), the things that are easy to get wrong (§9), and a one-paragraph summary (§10).

For build sequencing, see [AGENTS.md §11.10](../AGENTS.md#1110-phases) — the phase list there is the authoritative roadmap and this spec deliberately does not duplicate it.

The **scenario-specific** counterpart for the in-scope SOW workflow lives at [`scenarios/sow-response.md`](scenarios/sow-response.md); the skill body itself ([`agent/skills/sow-response/SKILL.md`](../agent/skills/sow-response/SKILL.md)) is the binding contract when these documents disagree.

---

## 2. The problem this solves

### 2.1 The shape of work being replaced

Cross-functional projects inside large organisations follow a recognisable pattern: a senior person (programme manager, deal lead, SOW owner, audit coordinator) is responsible for delivering a consolidated artifact by a deadline. The substance of the artifact comes from several other senior people, each contributing a section. The coordinator's actual job is:

- Decomposing the deliverable into sections/tasks and assigning each to the right owner.
- Communicating expectations clearly across email, Teams, calendar invites, task lists.
- Watching for delivery across heterogeneous channels (people deliver however they prefer — email attachment, OneDrive link, Teams message, SharePoint upload, sometimes typed inline).
- Reading what comes in and judging whether it meets the runbook's expectations.
- Nudging gently when things are late, intelligently when people are unavailable.
- Flagging gaps and inconsistencies.
- Consolidating everything into the final deliverable.

Today this is done with a frantic SharePoint folder, an Excel tracker, a dozen Teams threads, and the coordinator's own mental model. It's exhausting, error-prone, and the coordinator's time is mostly spent on coordination overhead rather than judgement.

### 2.2 What this system does instead

The coordinator interacts with a single Foundry-hosted agent through a desktop client. The agent runs the coordination — sets up the structure, watches for deliveries across all channels, reads what arrives, checks against the runbook, flags gaps, drafts suggested follow-ups for the coordinator's approval, and consolidates the final artifact.

The coordinator's job becomes: define the project, ratify the agent's plan, make the judgement calls the agent surfaces, approve outbound communications. The agent does the rest.

### 2.3 The in-scope scenario — SOW Response

Used throughout this spec as the worked example, and the only scenario shipped today.

An SOW Owner at an ITES enterprise responds to a customer RFP by compiling a **Statement of Work**. They open the desktop client and type:

> *"I've received an RFP from Contoso. The Teams call with the internal stakeholders happened today. Go and pull the details from it and get started."*

The agent grounds the prompt in WorkIQ — finds the cited Teams meeting, resolves the RFP file from email/SharePoint/OneDrive, resolves collaborator UPNs and `is_external` flags — and proposes a **Project Charter**: one task per SOW section (technical scope, PM scope, commercial, case studies, …), each with an owner, deadline, and section-specific runbook requirements pulled from the RFP. The SOW Owner ratifies or amends.

The agent then fans out the kickoff — Teams DMs to internal collaborators, emails to externals, per the [communication matrix](../agent/skills/sow-response/references/COMMUNICATION_MATRIX.md). Over the following days, collaborators deliver however they prefer. The agent captures each delivery, reads it via WorkIQ, surfaces highlights and runbook gaps, drafts nudges for approval when people are late, handles reassignments cleanly when someone is unavailable. When all sections are in, it consolidates the final SOW Word document. The SOW goes to the customer; the working state dissolves.

The full skill body is at [`agent/skills/sow-response/SKILL.md`](../agent/skills/sow-response/SKILL.md); the scenario-flavoured walk-through is at [`scenarios/sow-response.md`](scenarios/sow-response.md).

### 2.4 Why the agent is still generic

The architectural separation matters: the agent's code is project-shape-agnostic. The same code, with a new skill, would also handle quarterly budget consolidation, audit responses, all-hands deck assembly, M&A diligence response, regulatory inquiries, board packs, new-hire onboarding execution. What changes between scenarios is (a) the **skill body** and (b) the *content* of the **Charter** the skill produces.

Today only the SOW skill ships, but the design preserves that future optionality.

---

## 3. Architecture overview

### 3.1 The three components

```
Desktop client  ──HTTPS──►  Foundry Hosted Agent
(MSAL public                │  (one deployment;
 client / az login)         │   per-project sessions)
                            │
User bearer in              │  ┌──────────────────┐
Authorization;              ──┤ Session sandbox  │
project_id in                 │ ($HOME persists; │
x-agent-chat-isolation-key    │  Charter,        │
and ?agent_session_id         │  state, logs)    │
                              └──────────────────┘
                                     │
                                     ▼
                                 WorkIQ MCP servers via the Foundry Toolbox
                                 (called in the calling user's identity via
                                  OAuth Identity Passthrough on the
                                  Toolbox connections; reads M365 data)
```

### 3.2 Why each component, and what it does

**Desktop client — the user's surface.** A local app that signs the user in (MSAL public client, `az login`, or Windows broker), POSTs prompts to the agent's `/responses` endpoint with the user's bearer in `Authorization`, and renders the streamed SSE output. Today's reference implementation is the spike under [`../spike/desktop_to_foundry/`](../spike/desktop_to_foundry/). The client holds no project state; it is a thin shell over the agent.

**Foundry hosted agent — the workspace's brain.** One deployed agent. Each project is a separate session in this agent, keyed by chat-isolation key (the project ID). State lives in `$HOME` of each session's microVM sandbox. Sessions persist up to 30 days with 15-minute idle timeout (compute deprovisions, `$HOME` state is preserved, resume is automatic on next request). The agent's code is project-shape-agnostic — it operates against a Project Charter (§6) using the skill(s) loaded at boot (§7).

**WorkIQ MCP servers — the M365 data plane.** Used for reading email, Teams chats, SharePoint, OneDrive, Calendar, and for sending Teams messages and emails on a user's behalf. WorkIQ uses **delegated user authentication only** — there is no application-only mode. Every WorkIQ call runs in the OBO context of the calling user, propagated by the Foundry platform via OAuth Identity Passthrough on the Toolbox connections.

### 3.3 The key architectural separation

> **The agent's code is generic. The scenario's procedure lives in the skill. The project's specifics live in the Charter.**

This three-layer separation is what makes the system handle any scenario without per-scenario code changes. The agent's code never has to know what an "SOW" or a "board pack" specifically means — it knows how to run a skill against a Charter and persist state. The skill carries the scenario's procedural knowledge. The Charter carries the per-project content.

### 3.4 What is explicitly NOT in this architecture

These are deliberate exclusions; the implementing agent should not introduce them.

- **No cron jobs, no scheduled tasks, no background workers.** The agent only acts when a user invokes it. WorkIQ cannot run on a service identity, so autonomous background activity would lose the user context that makes WorkIQ work at all.
- **No per-project deployment.** Per-project specificity comes from session state inside the sandbox, not from infrastructure.
- **No project database, no message queue, no event bus.** `$HOME` in the session sandbox is the project's state store.
- **No exposing arbitrary ports from the sandbox.** Foundry hosted agents only serve `/responses`. The client reaches the agent through that gated endpoint and nothing else.
- **No autonomous outbound communications.** Every Teams message, email, or task sent to a stakeholder must be human-approved by the SOW Owner. The agent drafts; the human approves; the agent sends in the human's identity (the bearer the client attached to `/responses`, propagated by the Foundry platform).
- **No second runtime, no in-process code generation.** Exactly one runtime: a Microsoft Agent Framework `Agent` on a Foundry `gpt-5.x` deployment. The skill writes the final deliverable declaratively via WorkIQ Word/SharePoint tool calls. See [AGENTS.md invariant 12](../AGENTS.md#3-non-negotiable-architectural-invariants).

---

## 4. Technology stack

### 4.1 Microsoft Foundry hosted agents

The agent is deployed once as a container image to Azure Container Registry, then registered with Foundry Agent Service via `agent/scripts/deploy.py`. The platform provisions per-session microVM sandboxes on demand and handles lifecycle.

Key properties to rely on:

- **Per-session sandbox isolation.** Hypervisor-isolated microVM per session. Each session gets its own `$HOME` directory that persists across idle/resume cycles. No cross-session access.
- **Session lifetime up to 30 days, 15-minute idle timeout.** Compute deprovisions on idle; state is preserved; resume is automatic on next request. Cold-start latency is acceptable for human-driven interaction (single-digit seconds).
- **Isolation keys for session scoping.** `x-agent-chat-isolation-key` is set by the client to `project_id`, giving each project a stable Foundry session.
- **Responses protocol.** The agent serves the OpenAI-compatible `/responses` endpoint via `agent-framework-foundry-hosting.ResponsesHostServer`. Multi-turn continuity is the caller's responsibility (pass `previous_response_id`). The host server emits the standard OpenAI SSE event stream and root OpenTelemetry spans automatically.
- **OAuth Identity Passthrough at the Toolbox connection layer.** The end-user's bearer (attached to `/responses` by the client) is exchanged by the Foundry runtime per Toolbox connection — emitting an `oauth_consent_request` SSE event the first time per (user, connection) so the user can consent in a browser. The agent process holds no user tokens. See [AGENTS.md invariant 3](../AGENTS.md#3-non-negotiable-architectural-invariants).
- **Agent identity and observability.** Each agent gets a Foundry-assigned Managed Identity. Application Insights connection string is injected automatically; OpenTelemetry tracing works out of the box.

### 4.2 Host runtime — Microsoft Agent Framework

The agent runs **Microsoft Agent Framework** (`agent-framework-core` + `agent-framework-foundry` + `agent-framework-foundry-hosting`) on top of a Foundry `gpt-5.x` deployment authenticated by the Foundry-assigned Managed Identity. There is exactly one `Agent` instance per process, kept warm for the lifetime of the container, with the loaded skill body as `instructions` and the WorkIQ Toolbox + agent-side `$HOME` state tools as `tools`. The model picks tools from the prompt; there is no verb dispatcher.

Implementation details — module ownership, env-var policy, MCP wiring, tracing — are in [AGENTS.md §4](../AGENTS.md#4-technology-choices-locked) and [§11.5–§11.6](../AGENTS.md#115-agent-boot-sequence). Do not reintroduce a second runtime (Copilot SDK, separate Anthropic key, parallel reasoning client) — see [AGENTS.md invariant 12](../AGENTS.md#3-non-negotiable-architectural-invariants).

### 4.3 WorkIQ MCP servers via Foundry Toolbox

All WorkIQ MCP servers (Mail, Calendar, Files, Teams, User, Copilot, plus a general `workiq.ask`) are bundled in a single Foundry Toolbox named `Charter-Agent-Tools`. The agent attaches the Toolbox as one raw MAF `MCPStreamableHTTPTool`; MAF handles the MCP `initialize` / `tools/list` / `tools/call` plumbing.

Critical constraints:

- **Delegated permissions only — no application-only mode.** Every WorkIQ call runs as the currently-signed-in user, via OAuth Identity Passthrough on the Toolbox connections.
- **Microsoft 365 Copilot license required** for every user who interacts with the system. Without it, WorkIQ calls fail for that user.
- **One-time tenant admin setup** — registering Agent 365 service principals (the `Enable-WorkIQToolsForTenant.ps1` step) — must already be done. If WorkIQ is already callable from a Foundry portal agent in your tenant, this is confirmed.
- **OAuth Identity Passthrough must be enabled on the MCP connections** in the Foundry portal. If a Foundry-portal playground call to a WorkIQ tool returns *your* data, passthrough is working.
- **No custom client-side app registration is required.** The desktop client uses a pre-existing public client (Azure CLI's `04b07795-…` or any other pre-consented public client) with the `https://ai.azure.com/.default` scope. The WorkIQ scopes are pre-granted at the Foundry / Agent 365 platform level on the project's Toolbox connections.
- **100-second non-streaming MCP-call timeout.** Any single WorkIQ call must complete well under that bound; fan out polls concurrently rather than serially.

Net: if you can already see a Foundry-portal agent making WorkIQ Mail/Calendar/Teams calls successfully, the tenant-level setup is sorted. No additional identity work is required.

Full wire-shape contract (MAF `MCPStreamableHTTPTool`, header injection via `httpx` event hooks, `load_prompts=False`, etc.) is in [AGENTS.md §4.1](../AGENTS.md#41-foundry-toolbox-via-native-maf-mcpstreamablehttptool).

### 4.4 Desktop client

The only client surface this sample ships is the desktop client under [`../spike/desktop_to_foundry/`](../spike/desktop_to_foundry/). It signs the user in locally via the Azure CLI public client (`04b07795-8ddb-461a-bbee-02f9e1bf7b46`) or any other pre-consented public client, acquires a bearer for the `https://ai.azure.com/.default` scope, and POSTs prompts to the agent's `/responses` endpoint.

Responsibilities:

- Acquire the user's bearer locally (MSAL public client / `az login` / Windows broker — no custom app registration).
- POST `{input, previous_response_id?, stream: true}` to `/responses` with `Authorization: Bearer <user_token>`, `x-agent-chat-isolation-key: <project_id>`, and `?agent_session_id=<project_id>` so each project gets a stable Foundry session.
- Stream the standard OpenAI Responses SSE events back to the user (`response.created`, `response.output_text.delta`, tool-call events, `response.completed`).
- Handle the `oauth_consent_request` SSE event — open the carried Microsoft login URL in a browser, wait for the user to consent, then re-POST the same prompt with `previous_response_id` set to the in-flight response so Foundry can resume the same turn against WorkIQ.
- Render the agent's text + tool outputs as the source of truth. The client holds **no** project state.

The agent code does not care what kind of client is in front of it — anything that can attach a user bearer to an HTTPS POST and stream SSE works.

### 4.5 Azure Entra ID

App registrations needed: **only the agent's Foundry-assigned Managed Identity** (created automatically by Foundry at deployment).

The desktop client uses a pre-existing public client, so no custom client-side registration is required. The WorkIQ delegated scopes are pre-granted at the Foundry / Agent 365 platform level on the project's WorkIQ MCP connections, so no WorkIQ-specific permissions need admin consent.

Conditional Access: the agent's Managed Identity and the user's interactive sign-in are both subject to Conditional Access policies. The Foundry `oauth_consent_request` SSE flow surfaces CA challenges naturally.

### 4.6 SharePoint / Microsoft 365

The system reads from and writes to whatever SharePoint sites the project lives in. No special site setup is required beyond standard M365 governance — the skill drives the WorkIQ Files tools to create the project folder, set up templated draft files where appropriate, and export the final deliverable. Permissions are managed through standard WorkIQ Files / Graph calls.

### 4.7 Observability and audit

- Application Insights connection string is auto-injected into the agent container; the Foundry instrumentor wires the OpenTelemetry exporter automatically. Use the `@trace_function` decorator re-exported from `observability` for custom spans.
- Every state-mutating step the skill takes calls `log_workflow_step(...)` which appends to `$HOME/activity.json` (NDJSON, one object per line). This is the project's auditable record and the data source for the activity stream the client renders.
- The Charter + activity stream together form a recoverable audit trail of any project.

---

## 5. The user experience

### 5.1 The single human role — SOW Owner

The dashboard has exactly one human user — the **SOW Owner** — who ratifies the Charter and drives the project from the desktop client. The agent fans out kickoff messages, nudges, emails, Teams messages, and tasks to collaborators on M365 in the SOW Owner's identity, but collaborators do **not** sign in to the client. Their experience is "I got a Teams DM / email from the SOW Owner; I replied"; they never see this system directly.

### 5.2 The SOW Owner's interactions

- **Free-text instructions to the agent.** The desktop client is a chat surface. The SOW Owner types kickoff prompts, status questions, amendments, and approval decisions.
- **Charter ratification at kickoff.** The agent proposes a Charter; the SOW Owner ratifies or asks for amendments. Nothing is fanned out until ratification.
- **Approval of drafted outbound actions.** The agent never sends a Teams message, email, or task autonomously. It drafts and surfaces; the SOW Owner approves (or dismisses); the agent then sends in the SOW Owner's identity.
- **Mid-project amendments.** "Reassign Talent to Sofia." "Add a vendor-risk section to the SOW." "Change the deadline." The agent re-ratifies the affected Charter slice before acting.

### 5.3 The on-visit refresh model

The agent only acts when invoked. On every `/responses` turn the SOW Owner sends, the skill body runs whatever capture-and-status loop is appropriate for the prompt — typically polling each in-flight task's channels via WorkIQ, classifying new events, updating `project_log.json`, then summarising back to the SOW Owner. There is no background polling, no scheduled wake-up.

The 15-minute idle timeout means a first turn after a long pause may take 2–5 seconds to warm up; the client should show a "warming up…" indicator and then stream as normal.

---

## 6. The Project Charter — the system's most important artifact

### 6.1 What the Charter is

`$HOME/project_charter.md` — a Markdown document the agent writes at kickoff that defines the project's goal, the deliverable, the tasks (one per SOW section), the owners and their deadlines, the per-task runbook requirements pulled from the RFP, the channels the agent will watch for delivery, and the communication mode (Teams DM vs email) for each collaborator.

It is written by the skill at kickoff, ratified by the SOW Owner before any kickoff fan-out happens, and treated as immutable thereafter (amendments require an explicit re-ratification flow — §6.4).

Every later step the skill takes reads the Charter to decide what to do. The Charter is the project's constitution.

### 6.2 Schema

There is **no** Pydantic schema for the Charter today. The shape is owned by the skill body — see [`sow-response/SKILL.md` §5a (charter template)](../agent/skills/sow-response/SKILL.md) for the authoritative layout. The companion file `$HOME/project_log.json` carries the per-task working state (status, submissions, kickoff-sent flags, channel cursors); its shape is owned by [`SKILL.md` §5b](../agent/skills/sow-response/SKILL.md).

A future scenario (board pack, audit response, …) is free to define a completely different charter shape and log shape; the agent code never inspects either file — it goes through the generic `state_read_*` / `state_write_*` tools. The skill is the schema contract.

### 6.3 How the Charter is created

The kickoff flow (in detail at [`sow-response.md`](scenarios/sow-response.md) and [`SKILL.md` §2–§7](../agent/skills/sow-response/SKILL.md)):

1. **SOW Owner types a kickoff prompt** referencing a recent RFP and a triggering meeting/email.
2. **Agent grounds the prompt in WorkIQ.** One `workiq.ask` call for open-ended discovery, then drills into the cited Teams meeting and the RFP file via the typed WorkIQ tools (Mail/SharePoint/OneDrive/Teams/Calendar). Resolves collaborator UPNs and `is_external` via the User tool.
3. **Agent proposes a Charter** populated from the grounding. Surfaces it to the SOW Owner with explicit notes on which sources it used and which plausible alternatives it set aside.
4. **SOW Owner ratifies or amends.** Amendments re-run the propose step.
5. **Charter is locked.** Written to `$HOME/project_charter.md`. `project_log.json` is initialised with one task entry per SOW section.
6. **Kickoff fan-out executes against the locked Charter.** Per the [communication matrix](../agent/skills/sow-response/references/COMMUNICATION_MATRIX.md), the skill sends Teams DMs to internals and emails to externals, then read-modify-writes `project_log.json` to record `kickoff_sent` per task. Every step appends to `$HOME/activity.json` via `log_workflow_step`.

### 6.4 Charter amendment

When the SOW Owner says *"add a vendor-risk section, owned by Marcus"* mid-project, the skill:

1. Reads the current Charter and validates the amendment (no orphan dependencies, no conflict with completed work).
2. Proposes the amended Charter to the SOW Owner for ratification.
3. On ratification, writes the new Charter and updates `project_log.json` (adds the new task row).
4. Fans out the new task's kickoff actions (briefing on the right channel per the communication matrix).
5. Logs the amendment to `$HOME/activity.json` with the SOW Owner's identity.

---

## 7. Scenarios as Agent Skills

### 7.1 The skill-based specialisation model

A scenario is one Agent Skill under [`../agent/skills/{name}/`](../agent/skills/) with a `SKILL.md` body (per the [agentskills.io spec](https://agentskills.io/specification)) plus optional `references/`, `scripts/`, `assets/` subdirs. The skill is auto-loaded at boot by [`runtime/skill_loader.py`](../agent/src/charter_agent/runtime/skill_loader.py) and injected as the warm host `Agent`'s `instructions`. The host model selects among loaded skills based on each skill's `description` (which must say *what* it does and *when* to use it, with trigger keywords). Today there is only one — `sow-response` — so the routing is trivial.

A skill's `SKILL.md` describes:
- The triggering condition (when the host model should activate this skill).
- The reasoning steps it should walk through (grounding, charter proposal, kickoff fan-out, capture, classification, drafting, consolidation, closure).
- The state files it reads/writes and the JSON/Markdown shapes for each.
- The hard rules (e.g. "never auto-send a Teams message without explicit user OK").

Skills travel with the agent image. They are reviewed and shipped as code changes; CI runs `skills-ref validate` on every PR that touches `agent/skills/`. See [AGENTS.md §4.3](../AGENTS.md#43-agent-skills-format-agentskillsio-conformance) for the format contract and [§4.4](../AGENTS.md#44-core-code-vs-skill--the-decision-rule) for the explicit decision rule between skills and code.

### 7.2 The in-scope skill — `sow-response`

End-to-end SOW response workflow:

- **§1 mode-detect.** First-run vs resume, based on whether `project_log.json` exists in `$HOME`.
- **§2–§7 first-run.** Ground from triggering email/meeting/prior artifact → propose Charter → ratify with SOW Owner → fan out kickoff to internal collaborators (Teams DM) and externals (email) per the communication matrix → persist `project_charter.md` + initial `project_log.json` → return a ≤4-sentence closing receipt to the SOW Owner.
- **§8 resume.** Reload state, summarise where the project stands, take the next instruction.
- **§9 capture & classify.** Poll each in-flight task's Mail/Teams channels via WorkIQ for new replies; classify each candidate event per [`references/CLASSIFICATION_RUBRIC.md`](../agent/skills/sow-response/references/CLASSIFICATION_RUBRIC.md); update task status in `project_log.json`; surface drafted nudges / clarifications / reassignments to the SOW Owner for approval; never auto-send.
- **§10 must-NOT rules.** Hard floors: no auto-send, no Teams/SharePoint sharing with externals (email only), no fabrication of facts not grounded in WorkIQ output.

Supporting references the skill progressively-discloses on demand: [`SOW_SECTIONS.md`](../agent/skills/sow-response/references/SOW_SECTIONS.md), [`COMMUNICATION_MATRIX.md`](../agent/skills/sow-response/references/COMMUNICATION_MATRIX.md), [`CLASSIFICATION_RUBRIC.md`](../agent/skills/sow-response/references/CLASSIFICATION_RUBRIC.md).

### 7.3 Adding a new scenario

To add a board-pack / audit / all-hands scenario later: add `agent/skills/<name>/SKILL.md` with a clear `description` (including trigger keywords) and any required `references/*.md`. The host model selects among skills automatically. The agent code does not change. The scenario spec (the human-readable companion to the skill body) lives at [`scenarios/<name>.md`](scenarios/) following the pattern of [`scenarios/sow-response.md`](scenarios/sow-response.md).

---

## 8. The on-visit capture loop

This is the operational heart of the system. It runs as part of the `sow-response` skill body (§9 of the skill) — there is no separate `capture/` Python module today.

### 8.1 The cycle, on each /responses turn

On every prompt the SOW Owner sends after kickoff has been fanned out:

1. **Load Charter and log** from `$HOME` via `state_read_text` / `state_read_json`.
2. **For each in-flight task** (status != `submitted` and != `closed`):
   - Poll the task's watch channels via WorkIQ — Mail (for replies from the owner, including any external thread the kickoff started) and Teams DM (for internal owners) — using a `since` cursor recorded in the previous turn.
   - Dedupe candidate events by `internetMessageId` (Mail) or message ID (Teams) against `project_log.json`'s `submissions[]` for that task.
   - For each new event, classify per [`CLASSIFICATION_RUBRIC.md`](../agent/skills/sow-response/references/CLASSIFICATION_RUBRIC.md): `submission` / `revised_submission` / `question` / `supporting_material` / `unrelated`. Ambiguous cases get a `needs_review` flag.
   - If `submission`: extract content via WorkIQ; check against the task's `runbook_requirements`; update task status.
3. **Re-summarise project status** for the SOW Owner — what's in, what's pending, what's at risk, what needs the SOW Owner's attention (questions, gaps, missed deadlines).
4. **Draft suggested actions** (nudges, clarifications, reassignments) — never sent autonomously; the SOW Owner approves the next turn.
5. **Persist** the updated `project_log.json` and append to `$HOME/activity.json`.

### 8.2 Capture across channels

For each task the agent watches the channels listed in the Charter — at minimum the kickoff thread (Teams DM for internals, email for externals). The skill drives the WorkIQ tools through the Toolbox; there is no separate channel-handler registry today (the skill body is the registry). If polling logic ever needs to be promoted to Python — for cursor correctness or because a non-skill consumer needs it — it lands as a `capture/handlers/*` module per the §4.4 decision rule in AGENTS.md.

### 8.3 The suggested-action layer

When the agent identifies something the SOW Owner needs to do — nudge an owner, ask a clarifying question, reassign a task — it doesn't act autonomously. It drafts the message, surfaces it in the response with the recipient and the reasoning, and waits. When the SOW Owner approves in the next turn, the skill executes the action via WorkIQ in the SOW Owner's identity (propagated by the Foundry platform — invariant 3), so the Teams message or email is sent as the SOW Owner, not as a bot.

This human-in-the-loop for outbound is the system's core governance property. There is no auto-approve mode. See [AGENTS.md invariant 5](../AGENTS.md#3-non-negotiable-architectural-invariants).

---

## 9. Things easy to get wrong

These are the gotchas, design subtleties, and constraints that don't show up in feature lists but will break the system if forgotten.

### 9.1 Identity is the single most important thing to get right

Every WorkIQ call must run in the calling user's identity, propagated by the Foundry platform's OAuth Identity Passthrough. Never use the agent's Managed Identity for content access. Microsoft 365's permission model, sensitivity labels, and compliance policies are enforced at the user level; using the agent's identity would either fail (the agent has no M365 license) or bypass user-level governance, which is a serious security and compliance issue. WorkIQ's docs are explicit that application-only auth is not supported.

When the SOW Owner approves an outbound action, that action runs in *her* identity. The Teams message is sent as her. The email is sent as her. Do not send anything as the agent identity to humans — that breaks the trust model and looks like spam.

### 9.2 Idempotency on every outbound side-effect

The desktop client may retry a turn on a network error. The skill may decide to fan out the same kickoff twice if the previous run was interrupted mid-write. Make sure executing the same approved action twice doesn't send two Teams messages. The pattern today: the skill dedupes by `internetMessageId` / Teams message ID in `project_log.json` before sending. If an `actions/` module is ever extracted, it should carry a structured `state.executed_action_ids` set with the same property.

### 9.3 The Charter is immutable except via the amendment flow

Don't let any path write to `project_charter.md` except the kickoff flow and the amendment flow. Both must run through ratification. The Charter being trustable is the foundation for everything else; quiet drift would be a catastrophic bug. Track the Charter version in `project_log.json` and bump it on every amendment.

### 9.4 Watch the 15-minute idle timeout

Cold-starting from idle is fast but not instant. The desktop client should show a "warming up" state for the first 2–5 seconds of any turn that follows a long idle. Don't show a stale screen during cold-start.

### 9.5 30-day session lifetime is a hard ceiling

If a project legitimately runs longer than 30 days, plan for it: at the 25-day mark the agent should proactively offer to "renew" — which means kicking off a new session keyed by a new `project_id`, migrating `project_charter.md` + `project_log.json` + `activity.json`. Don't let projects silently die at day 30.

### 9.6 Do not expose ports from the sandbox

The sandbox is not a web server publishable to the public internet. The agent's only public surface is the Foundry-gated `/responses` endpoint; the client reaches it through that. Don't try to expose Streamlit or any other server from inside.

### 9.7 The watch-channel set needs to be extensible

Different scenarios may need different channel kinds over time (a Jira watch channel for engineering projects, a Slack watch channel if you ever integrate). Today the channel set is hard-coded in the `sow-response` skill body (Mail + Teams DM). If/when a channel-handler registry is lifted into Python, design it as a registry of handlers keyed on `channel.kind`, with each handler implementing a common `poll(charter, task, since) -> list[CandidateEvent]` interface. Don't hardcode the channel types as inline `if channel.kind == "...":` switches.

### 9.8 Build for `linux/amd64` — Apple Silicon will catch you

Foundry hosted agents require `linux/amd64` images. If you build locally on an Apple Silicon Mac (or any ARM64 host), the default `docker build` produces an ARM64 image that fails at runtime in Foundry. The error message is not always obvious — it can look like the agent starts but crashes on first request.

Two ways to avoid this:

- **Preferred:** Use `az acr build --registry <acr> --image charter-agent:vN --file agent/Dockerfile agent/`. ACR's remote build always produces the right architecture.
- **If building locally:** Force the platform with `docker build --platform=linux/amd64 -t image .`. Works on ARM64 hosts via Docker's emulation.

### 9.9 Real classification will sometimes be wrong

The "is this email a submission?" classification will occasionally misfire. Always surface *what was captured* and *from where* in the agent's reply, and accept SOW Owner overrides cleanly ("that's not the submission, ignore it"). The skill should re-mark the task as pending and exclude that source on the next poll.

### 9.10 Audit-log everything

Every state mutation, every outbound action, every Charter amendment — log to `$HOME/activity.json` via `log_workflow_step` AND emit an OpenTelemetry span. The audit log is the project's auditable record. In regulated industries, this is the difference between adoptable and not.

### 9.11 The project_id needs to be URL-safe and human-readable

`contoso-sow-2026-05` not `a8c7f23e9b1d4f5a`. The SOW Owner may reference this in many places; an opaque ID looks suspicious. Generate slug-style IDs from the project goal at kickoff and ensure uniqueness within the tenant.

### 9.12 Handle the "SOW Owner forgot to ratify" case

If the SOW Owner types a kickoff prompt then closes the client before ratifying, the session sits with a proposed-but-unratified Charter. On the next turn, the agent should show the pending ratification, not stale state.

### 9.13 Licensing and tenant setup prerequisites

These are real operational dependencies but most are likely already in place if your tenant has been using WorkIQ at all:

- **Microsoft 365 Copilot license** for every user who will interact with the agent. Without it, WorkIQ calls fail for that user.
- **Agent 365 tenant enablement** (`Enable-WorkIQToolsForTenant.ps1`) must have been run by a tenant admin once. **Verification**: if any Foundry-portal-created agent in your tenant can already call WorkIQ tools, this is done.
- **Foundry project WorkIQ MCP connections** with OAuth Identity Passthrough enabled. Configure once per Foundry project via the portal.
- **A pre-consented public client for the desktop client's user sign-in.** The Azure CLI public client (`04b07795-…`) is what the spike uses. Any other pre-consented public client that can request the `https://ai.azure.com/.default` scope will work. **No custom app registration is required**, and no admin consent for WorkIQ-specific scopes is needed.

Do NOT plan for: a custom app registration with `WorkIQAgent.Ask` or per-WorkIQ-server delegated permissions. That pattern is for non-Foundry third-party agents calling WorkIQ directly. Foundry-hosted agents using portal-configured Toolbox connections skip this entirely — the platform pre-grants those scopes for you.

### 9.14 Be explicit about what the agent will NOT do

In the kickoff confirmation step, surface the agent's boundaries clearly: it will not autonomously contact owners; it will draft and surface for approval; the SOW Owner can reassign, amend, or close at any time. This sets the right expectations from minute one.

### 9.15 Don't store M365 content in `$HOME` long-term

Extracted content from WorkIQ should be summarised into `project_log.json` (highlights, compliance results) but the raw extracted content should be ephemeral — re-fetched on demand. Storing copies of email bodies, file contents, etc. in the sandbox creates a data-residency and retention problem you don't need.

---

## 10. The functional spec, summarised

> The SOW Owner types a kickoff prompt into a desktop client. A Foundry-hosted agent grounds the prompt in WorkIQ — pulling details from the triggering Teams meeting, the RFP file, and the collaborator directory — and proposes a structured **Project Charter** (one task per SOW section, each with owner, deadline, runbook requirements). The SOW Owner ratifies. The agent fans out the kickoff: Teams DMs to internal collaborators and emails to externals per the communication matrix, in the SOW Owner's identity (propagated by the Foundry platform via OAuth Identity Passthrough).
>
> Collaborators reply via their normal channels. On every subsequent turn from the SOW Owner, the agent polls each task's Mail/Teams channels via WorkIQ, classifies new events, updates the per-task status in `project_log.json`, and summarises back. It additionally drafts suggested actions — nudges, clarifications, reassignment proposals — for the SOW Owner's approval. Approved actions execute in the SOW Owner's identity.
>
> When all sections are in, the agent consolidates the final SOW Word document declaratively via WorkIQ Word/SharePoint tool calls and exports it. The project is closed; the working state in the Foundry session dissolves cleanly.
>
> The agent's code is generic across scenarios — today only the `sow-response` skill ships, but board pack, audit response, M&A diligence, and others would land as additional skills with no change to the agent itself. Per-project variety is captured entirely in the Charter; per-scenario procedural knowledge lives in the skill body.
