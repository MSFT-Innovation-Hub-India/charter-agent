# Charter Agent — Foundry Hosted Agent Reference Implementation

A production-grade reference implementation of a **Microsoft Foundry hosted agent** that autonomously orchestrates multi-week, cross-functional M365 workflows — without the user telling it what to do next.

> This is the canonical example of a Foundry hosted agent calling WorkIQ M365 Intelligence on behalf of the signed-in user via OAuth Identity Passthrough at the Foundry MCP connection layer.

---

## The scenario: why this agent is compelling

When an enterprise wins an RFP and needs to produce a Statement of Work, a Programme Manager (the "SOW Owner") faces a coordination challenge that spans days or weeks:

- Pull the RFP requirements and the meeting notes from Teams, email, or SharePoint
- Draft a project charter allocating each SOW section to an internal or external collaborator
- Fan out kickoff briefs — Teams DMs for internals, emails for externals — with per-section runbook requirements extracted from the RFP
- Wait days or weeks for replies to arrive across any channel (email, Teams, shared OneDrive docs)
- Chase up missing or incomplete submissions
- Validate each reply against the RFP requirements
- Consolidate everything into a final Word document

**The agent does all of this autonomously.** The SOW Owner's only job is the first prompt ("I received an RFP from Contoso — the kickoff call happened today, go get started") and occasional approvals before outbound messages are sent. Everything else — monitoring channels, classifying incoming content, updating status, drafting nudges, identifying gaps — the agent handles on its own, picking up exactly where it left off even after days of inactivity.

---

## The workflow lifecycle in detail

This section walks through what actually happens across the multi-week lifecycle of a single project. The agent is stateless between calls — it reconstructs its position entirely from files — but to the SOW Owner it behaves like a persistent coordinator that never forgets and never misses a channel.

### Day 0 — kickoff

The SOW Owner types a single message:

> *"We just had the kickoff call for the Contoso RFP. It came as a Teams meeting. Go pull the details and get us started."*

The agent runs its tool loop without any further prompting:

1. **Grounds** — calls WorkIQ Copilot to surface the kickoff meeting in the user's M365 estate. Reads the Teams meeting transcript to extract agreed owners and due dates. Reads the RFP document (from email attachment, SharePoint, or OneDrive — wherever it lives) to extract per-section requirements.

2. **Applies the two-source rule** — owners come exclusively from the meeting notes (never from the RFP, which names customer contacts, not internal staff). Requirements come from the RFP. Where these conflict, the agent flags the discrepancy before committing anything.

3. **Drafts the charter** — writes `project_charter.md` to the microVM sandbox. This is the ratified, immutable record of who owns what, with per-section runbook requirements extracted verbatim from the RFP language.

4. **Fans out** — sends a kickoff brief to every section owner. Internal collaborators (same Entra tenant) receive a Teams DM; external collaborators receive an email. Each brief contains the specific requirements that person's section must address. The agent **never sends without showing the SOW Owner the draft first** — outbound is approval-gated.

5. **Records state** — writes `project_log.json` (task list, kickoff timestamps, last-polled cursors) and appends to `activity.json` (the audit trail). Calls `publish_view` so the desktop dashboard widget updates immediately.

6. **Returns a closing receipt** — four sentences or fewer. "Charter committed. Four kickoff briefs sent (3 Teams DMs, 1 email). Awaiting submissions. I'll check back when you ask."

### Days 1–14 — monitoring (each time the SOW Owner checks in)

The SOW Owner doesn't need to say "check for replies" — a simple "how are we doing?" or "any updates?" is enough. The agent:

1. **Reads project state** — loads `project_log.json` to know what's been sent, what's been received, and when each task was last polled.

2. **Polls all channels simultaneously** — checks Mail, Teams, SharePoint, and OneDrive for activity since the last cursor, for each task owner. The M365 surface doesn't matter: a reply by email is treated the same as a Teams message or a shared OneDrive document. The agent's eyes span the entire collaboration surface.

3. **Classifies every item** — each piece of incoming content is classified against the CLASSIFICATION_RUBRIC:
   - **Submission** — content that actually addresses the section's runbook requirements. Validated against the requirements; gaps are flagged.
   - **Question** — the collaborator needs clarification before they can deliver. Surfaces to the SOW Owner.
   - **Supporting material** — relevant context but not the deliverable itself (e.g., a reference deck). Noted, not counted as submission.
   - **Unrelated** — noise. Ignored.

4. **Updates status** — marks tasks `submitted`, `submitted_with_gaps`, `in_progress`, `overdue`, or `at_risk` based on submission state and due dates. The communication matrix determines whether a follow-up goes by Teams DM or email.

5. **Drafts nudges** — for overdue or silent collaborators, the agent writes a follow-up message. It **never sends** — the nudge appears in the dashboard exceptions panel and waits for the SOW Owner to approve.

6. **Publishes the dashboard** — the desktop widget shows the full picture: which sections are in, which are missing, which have gaps, what the exceptions are.

### Weeks 2–3 — escalation and consolidation

As the deadline approaches:
- Tasks that remain unsubmitted transition to `overdue` → `at_risk`
- The exceptions panel surfaces the critical path to the SOW Owner
- Accepted submissions are assembled into the consolidated Word document via WorkIQ Word tools
- The deliverable URL appears on the dashboard once the document is created

### Context recovery after days idle

Between the SOW Owner's check-ins, the agent is completely dormant — no background threads, no scheduled jobs. When woken:

1. It reads `project_log.json` to know every task's current status, every submission's content ID, every cursor position (which messages have already been processed)
2. It reads the tail of `activity.json` — the chronological audit of everything that has happened — to understand the narrative of the project
3. It picks up exactly where it left off, without needing the SOW Owner to recap

This means the agent can be left alone for a week between check-ins and still give a coherent, accurate status update the moment it's prompted. The microVM filesystem is its memory.

---

### What makes this a compelling Foundry use case

| Capability | What it demonstrates |
|---|---|
| **Foundry Hosted Agent** | A containerised Python agent running as a first-class Foundry native service, not a wrapper |
| **Microsoft Agent Framework (MAF)** | Production-grade agent runtime; any popular framework can be used — MAF here because it ships with Foundry native tooling |
| **Foundry Toolbox (preview)** | A single MCP endpoint aggregating 8 WorkIQ M365 Intelligence services (135 tools) — Mail, Calendar, Teams, Files, Word, OneDrive, User, Copilot |
| **OAuth Identity Passthrough** | The agent calls M365 as the signed-in user — zero server-side secrets, zero OBO plumbing in the agent code |
| **Per-session microVM sandbox** | Every project gets an isolated persistent filesystem (`$HOME`); state survives container restarts |
| **Autonomous agent loop** | The model's tool-loop runs the entire workflow — grounding, charter writing, fan-out, monitoring — with no step-by-step prompting |
| **Context recovery from audit log** | After days idle, the agent reads its own activity log and project state to reconstruct exactly where it is in the workflow |
| **Foundry SDK telemetry** | App Insights spans, OTel traces, and a structured activity audit log — all auto-wired by the hosting framework |

---

## Documentation map

Start here, then follow the links that match your task:

| Document | What it covers | Read when |
|---|---|---|
| **This file** | Overview, scenario, capabilities, quick start | Always |
| [`agent/README.md`](agent/README.md) | Agent architecture, MAF + Foundry packaging, Toolbox, telemetry, session/sandbox model, local development, deployment | Building or modifying the agent |
| [`desktop-client/README.md`](desktop-client/README.md) | Desktop app, authentication flow, cache and local storage, SSE streaming, dashboard rendering, connection status | Working on the client |
| [`AGENTS.md`](AGENTS.md) | **Operating contract** — non-negotiable invariants, conventions, change-safety checklist | Before every non-trivial change |
| [`architecture/architecture_and_design.md`](architecture/architecture_and_design.md) | Component diagram, state schema, Toolbox wiring, auth flow, observability, security | When implementing or extending |
| [`functional-specs/project_workspace_spec.md`](functional-specs/project_workspace_spec.md) | Requirements, scenarios, channel taxonomy, status semantics | Changing scope or behaviour |
| [`functional-specs/scenarios/sow-response.md`](functional-specs/scenarios/sow-response.md) | The SOW response scenario in detail | Understanding the workflow |
| [`agent/skills/sow-response/SKILL.md`](agent/skills/sow-response/SKILL.md) | The skill body (agent instructions) — authoritative contract for all behaviour | Changing what the agent does |

---

## System overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Desktop Client (pywebview)                                              │
│  • Signs user in via MSAL / Windows Account Manager (WAM)               │
│  • Attaches user bearer to every /responses POST                        │
│  • Renders SSE stream: text deltas, tool activity, dashboard widget     │
│  • Manages per-project session_id → Foundry microVM routing             │
└───────────────────────────┬──────────────────────────────────────────────┘
                            │ HTTPS POST /responses
                            │ Authorization: Bearer <user_token>
                            │ Body: {input, stream:true, agent_session_id}
                            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Microsoft Foundry — Hosted Agent                                        │
│                                                                          │
│  ResponsesHostServer (agent-framework-foundry-hosting)                   │
│    └─ MAF Agent (warm, one per skill)                                    │
│         ├─ FoundryChatClient → gpt-5.x (Managed Identity)               │
│         ├─ instructions: SKILL.md body                                   │
│         └─ tools:                                                        │
│              • MCPStreamableHTTPTool → Charter-Agent-Tools Toolbox       │
│              • state_tools (read/write/append $HOME files)               │
│                                                                          │
│  Per-project microVM sandbox ($HOME)                                     │
│    project_charter.md  project_log.json  activity.json                  │
│    agent_session/<id>.json                                               │
│                                                                          │
│  Foundry platform                                                        │
│    • Routes agent_session_id to the correct persistent microVM           │
│    • Validates user bearer; exchanges into WorkIQ identity on MCP calls │
│    • Emits oauth_consent_request SSE on first WorkIQ access per user    │
│    • Auto-instruments: App Insights spans, OTel traces                  │
└───────────────────────────┬──────────────────────────────────────────────┘
                            │ MCP (Streamable HTTP)
                            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Charter-Agent-Tools Toolbox (Foundry-managed, preview)                  │
│  Single MCP endpoint → 8 WorkIQ M365 Intelligence servers               │
│  Mail · Calendar · Teams · Files · Word · OneDrive · User · Copilot     │
│  135 tools — user identity from Foundry passthrough, not agent secrets  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## A generic agent platform, not a one-scenario tool

The agent framework code — the HTTP server, Toolbox wiring, state I/O, skill loader, telemetry — contains **zero SOW-specific logic**. The entire SOW response workflow lives in one declarative file: [`agent/skills/sow-response/SKILL.md`](agent/skills/sow-response/SKILL.md). Swap that file and the agent runs a completely different workflow.

This is the skills-based architecture's core promise: **adding a new scenario requires no framework code changes**. Drop a skill bundle into `agent/skills/` and it is automatically discovered, loaded, warm-started, and routed to at boot.

### What a skill consists of

```
agent/skills/<skill-name>/
├── SKILL.md        The agent's instructions for this workflow (required)
│                   Parsed as agentskills.io-conformant YAML frontmatter + Markdown body.
│                   The body becomes Agent.instructions at runtime.
└── tools.py        In-process Python tools specific to this skill (optional)
                    Registered as MAF @tool functions alongside the Toolbox tools.
```

That's it. The skill loader reads `SKILL.md`, validates the frontmatter, instantiates a warm MAF `Agent` with the skill's instructions and tools, and makes it available for routing. No changes to `foundry_host.py`, `responses_host.py`, `state.py`, or any other framework module.

### Skills that could be added with no framework changes

The SOW response workflow is one point in a large space of M365-native coordination scenarios. All of the following would be new `SKILL.md` files:

| Scenario | What the skill would do |
|---|---|
| **Project onboarding** | Greet a new employee, set up their M365 access, schedule intro meetings, share relevant SharePoint sites, track completion |
| **Contract review** | Pull a contract from SharePoint, fan out sections to legal/commercial/technical reviewers, consolidate markup into a final document |
| **Incident response** | On an alert, create a Teams channel, page the on-call, monitor the channel for resolution signals, draft a post-mortem |
| **Recruitment pipeline** | Track candidates through interview stages, coordinate panel schedules via Calendar, collect and classify feedback from interviewers |
| **Budget approval** | Route budget requests to approvers, monitor email/Teams for decisions, escalate overdue approvals, produce a summary report |
| **Customer onboarding** | Create SharePoint project site, assign tasks to the delivery team via Planner/Tasks, monitor completion, send milestone reports to the customer |

Each of these spans days or weeks, involves multiple people, and spans multiple M365 channels — exactly the class of workflow the Foundry Toolbox + MAF agent loop is designed for. None would require modifying the framework code.

### The routing mechanism

The `general` skill (the default) handles ambiguous or unrecognised prompts. When the model determines the user's intent matches a known skill, it calls `route_to_skill("sow-response")`. The client detects this routing event and automatically re-sends with the target skill, so the transition is invisible to the user — they see one seamless flow, not a "select a skill" step.

Skill routing is declared in the `description` frontmatter of each `SKILL.md` — plain English describing the trigger phrases. No code changes when a new skill is added; the general skill's routing logic reads descriptions at runtime.

---

## Quick start

**Run the agent locally** (still uses cloud Foundry model + Toolbox):
```powershell
cd agent
.\.venv\Scripts\Activate.ps1
python -m charter_agent
# → http://localhost:8088/responses
```

**Run the desktop client:**
```powershell
cd desktop-client
.\.venv\Scripts\Activate.ps1
$env:AGENT_ENDPOINT_HOSTED = "https://<your-deployed-agent>/responses"
python app.py
```

Full setup, deployment, and testing instructions:
- Agent: [`agent/README.md`](agent/README.md)
- Desktop client: [`desktop-client/README.md`](desktop-client/README.md)

---

## Repository layout

```
charter-agent/
├── agent/                    Python agent (MAF + Foundry hosting)
│   ├── src/charter_agent/    Agent package
│   │   ├── __main__.py       Boot entry
│   │   ├── state.py          Atomic $HOME I/O
│   │   ├── observability.py  OTel + activity log
│   │   └── runtime/          foundry_host, responses_host, skill_loader, state_tools, project_router
│   ├── skills/               Skill bundles (SKILL.md + tools.py)
│   │   ├── sow-response/     The SOW response workflow skill
│   │   └── general/          Default routing skill
│   ├── tests/                23 pure-Python tests (no network)
│   └── pyproject.toml
├── desktop-client/           pywebview rich client
│   ├── app.py                Auth, SSE streaming, Bridge (Python ↔ JS)
│   └── ui.html               Single-file UI (CSS + HTML + JS)
├── architecture/             Design documents and diagrams
├── functional-specs/         Requirements, scenarios, references
├── test-fixtures/            Sample RFP and meeting notes
├── AGENTS.md                 Operating contract
└── README.md                 This file
```

---

## Current status (May 2026)

- Agent deployed and verified on Foundry project `ocvp-agent-svc` (model `gpt-5.4`)
- Two skills ship: `sow-response` (full workflow) and `general` (routing / default)
- 23 tests pass — pure Python, no network required
- Desktop client: pywebview app, multi-project sidebar, SSE streaming, dashboard widget
- End-to-end verified: calendar query, kickoff fan-out (Teams DMs + email), submission capture
