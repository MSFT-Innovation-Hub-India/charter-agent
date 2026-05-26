# Charter Agent — Backend

The hosted agent component. A Python 3.12+ service that runs on Microsoft Foundry as a first-class native agent, exposing the OpenAI-compatible Responses protocol and orchestrating multi-week M365 workflows through the WorkIQ Toolbox.

> **Navigation:** [Root README](../README.md) · [Desktop client](../desktop-client/README.md) · [Architecture](../architecture/architecture_and_design.md) · [AGENTS.md](../AGENTS.md)

---

## Contents

1. [What it is](#1-what-it-is)
2. [Framework choice: Microsoft Agent Framework (MAF)](#2-framework-choice-microsoft-agent-framework-maf)
3. [Skills-based architecture](#3-skills-based-architecture)
4. [The autonomous agent loop](#4-the-autonomous-agent-loop)
5. [Session and sandbox model](#5-session-and-sandbox-model)
6. [Context recovery from the activity log](#6-context-recovery-from-the-activity-log)
7. [Foundry Toolbox — one MCP endpoint for all of M365](#7-foundry-toolbox--one-mcp-endpoint-for-all-of-m365)
8. [Identity and authentication](#8-identity-and-authentication)
9. [Telemetry and observability](#9-telemetry-and-observability)
10. [Local development](#10-local-development)
11. [Deploying to Foundry](#11-deploying-to-foundry)
12. [Testing](#12-testing)
13. [Module reference](#13-module-reference)

---

## 1. What it is

The agent is a **Foundry native hosted agent** — a containerised Python service managed by the Foundry runtime, not a chatbot wrapper. It:

- Serves the OpenAI-compatible **Responses protocol** on `/responses`, streaming SSE events to the desktop client
- Runs an **autonomous tool loop** through the Microsoft Agent Framework (MAF), calling WorkIQ M365 tools without step-by-step prompting
- Maintains **per-project persistent state** in an isolated Foundry microVM filesystem (`$HOME`)
- Is invocable both **locally** (for development) and as a **deployed Foundry service** — the surface and protocol are identical; only the endpoint URL changes

The agent has no skill-specific Python code. All workflow behaviour lives in declarative skill manifests (`SKILL.md` files). The Python code is generic plumbing: HTTP server, Toolbox wiring, state I/O, telemetry.

---

## 2. Framework choice: Microsoft Agent Framework (MAF)

Foundry Hosted Agents are framework-agnostic. You can build one using LangChain, Semantic Kernel, AutoGen, or any other agent framework — as long as you expose the Responses protocol endpoint that Foundry expects.

This implementation uses **MAF (`agent-framework-core` + `agent-framework-foundry`)** because:
- It ships first-class support for `FoundryChatClient`, `MCPStreamableHTTPTool`, and `ResponsesHostServer` — all Foundry-native primitives
- `ResponsesHostServer` handles session routing, history replay via `previous_response_id`, and SSE streaming without hand-rolled code
- The warm-Agent-per-skill pattern (one `Agent` instance built at boot per skill, reused across requests) eliminates cold-start latency

### Packaging as a Foundry native agent

A Foundry hosted agent is a Docker container that:
1. Exposes an HTTP server on the port Foundry assigns (default 8088)
2. Listens on `/responses` for POST requests following the OpenAI Responses protocol
3. Authenticates against the Foundry model deployment using the platform's **Managed Identity** (injected automatically — no secrets in the image)

The `Dockerfile` in `agent/` packages everything: the Python runtime, the venv with MAF + Foundry hosting dependencies, the skill bundles, and the `python -m charter_agent` entrypoint.

When Foundry receives an incoming request, it:
- Validates the user bearer token
- Routes the `agent_session_id` to the correct persistent microVM
- Injects the Managed Identity credential so the agent can call the Foundry model deployment
- Streams the SSE response back to the caller

The agent code sees none of this routing complexity — it just calls `FoundryChatClient` with the deployment name and lets the platform handle credential injection.

---

## 3. Skills-based architecture

All workflow behaviour is declared in **skill manifests** under `agent/skills/{skill-name}/`:

```
agent/skills/
├── sow-response/
│   ├── SKILL.md          Agent instructions (loaded as Agent.instructions at boot)
│   ├── tools.py          In-process Python tools (dashboard_payload, record_submission, etc.)
│   └── references/       Supporting reference docs (classification rubric, SOW sections, etc.)
└── general/
    ├── SKILL.md          Default skill for routing and general queries
    └── (no tools.py)
```

At boot, `skill_loader.py` reads every `SKILL.md`, validates its agentskills.io-conformant frontmatter, and constructs one warm **MAF `Agent`** per skill. Each Agent gets:
- `instructions` = the SKILL.md body (the declarative workflow description)
- `tools` = the Toolbox `MCPStreamableHTTPTool` + the skill's in-process Python tools + the shared `state_tools`

The model at runtime receives the skill's instructions and calls tools from its allowed list to execute the workflow — **no per-step Python orchestration code**. Adding a new skill is entirely additive: drop a `SKILL.md` into a new directory.

### Skill routing

Each incoming request carries a `[charter-agent-context: project_id=p-xxx skill=sow-response]` preamble. `project_router.py` strips this, sets the active project in `state.py`, and returns the resolved skill name. `responses_host.py` swaps `self._agent` to the warm Agent for that skill before invoking the MAF runtime.

---

## 4. The autonomous agent loop

The core insight: **the agent drives itself**. The SOW Owner's single prompt ("I received an RFP from Contoso, get started") triggers a MAF tool loop that:

1. **Grounds** — calls WorkIQ Copilot to find the kickoff meeting and RFP in the user's M365 environment
2. **Understands** — extracts owners, due dates, and requirements from Teams meeting transcripts and the RFP document
3. **Commits** — writes `project_charter.md` and `project_log.json` to the microVM filesystem
4. **Fans out** — sends kickoff briefs (Teams DMs for internals, emails for externals) and records `kickoff_sent` in the project log
5. **Monitors** — on subsequent visits, polls Mail, Teams, and Files for activity since the last cursor
6. **Classifies** — applies the CLASSIFICATION_RUBRIC to each incoming item (submission / question / supporting / unrelated)
7. **Updates** — writes submission records, adjusts task status, flags overdue items
8. **Drafts follow-ups** — writes suggested nudge messages (never sends without explicit user approval)
9. **Publishes** — calls `publish_view` to push the current dashboard state to the desktop client

The user never sees steps 1–9 as a sequence they have to drive. They see a response: "I've kicked off the SOW. Here's the charter. Four briefs sent." Then, days later: "Submissions received from 2 of 4. One is incomplete — I've drafted a follow-up for your approval."

**The agent loop is stateless between calls.** It reconstructs its position from `project_log.json` and `activity.json` on every turn. There are no background threads, no cron jobs, no webhook listeners. The user (or an automation) initiates each turn by posting to `/responses`.

---

## 5. Session and sandbox model

Understanding this model is essential before working with state.

### Two independent isolation layers

**Layer 1 — Foundry session → microVM (platform-managed)**

Every project in the desktop client sidebar has its own `agent_session_id`. When the client POSTs to `/responses` with that session ID, Foundry routes the request to a **dedicated persistent microVM** for that session. The microVM has its own `$HOME` directory — a private filesystem that persists across container restarts for the lifetime of the session (up to 30 days of inactivity).

The agent code never decodes a session ID into a path. It simply uses `os.environ["HOME"]`, which Foundry has already set to the microVM's sandbox directory before the agent process starts.

```
project-A  →  agent_session_id: ses-abc  →  microVM-1  →  $HOME = /home/agent-abc/
project-B  →  agent_session_id: ses-def  →  microVM-2  →  $HOME = /home/agent-def/
```

**Layer 2 — project_id → subfolder (agent-managed)**

Within a session's `$HOME`, the agent further organises state by project ID. `project_router.py` reads the `project_id` from the message preamble and calls `state.set_active_project_id(pid)`, which writes a pointer to `$HOME/.active_project`. Every subsequent `state.project_path(filename)` call resolves to `$HOME/projects/<pid>/filename`.

This two-layer model means:
- In **hosted mode**, each project is a separate microVM — projects are isolated at the platform level
- In **local dev**, all projects share one process and one `$HOME` (pinned to `agent/.charter-agent-home/`) — isolation is the subfolder only

### State files in `$HOME`

```
$HOME/
├── .active_project              One line: the current project_id
├── projects/
│   └── p-9bd98bc8/
│       ├── project_charter.md   Ratified charter (written once at kickoff, immutable)
│       ├── project_log.json     Live workflow state: tasks, submissions, cursors, status
│       └── activity.json        Append-only NDJSON audit trail
└── agent_session/
    └── <session-id>.json        MAF thread persistence (owned by MAF, not by us)
```

All reads and writes go through `state.py`, which:
- Resolves paths relative to `$HOME` and rejects any that escape it (`..`, absolute paths)
- Writes atomically: write to `<file>.tmp`, then `os.replace()` — no half-written files on crash or eviction

---

## 6. Context recovery from the activity log

After the agent has been idle for days (while waiting for collaborator submissions), it is woken by the next user message. It has no in-memory state. It recovers entirely from files:

1. `load_project_state` reads `project_log.json` — current task list, submission status, last-polled cursors
2. `state_read_text("project_log.json")` + `activity.json` give the full execution history
3. The model reads these, understands where the workflow is, and continues from exactly that point

`activity.json` is the structured audit trail — every tool call, status change, and outbound message is appended as an NDJSON line via `log_workflow_step()`. It serves three purposes:
- **Human audit**: exported to SharePoint at project close as the narrative record
- **Agent context recovery**: the model reads the last N entries to get up to speed
- **Dashboard rendering**: the desktop client reads the tail of this file for the activity stream panel

```jsonl
{"at":"2026-05-20T09:14:23+00:00","actor":"agent","kind":"kickoff.sent","summary":"Teams DM sent to alice@contoso.com — Technical Scope","ref":"p-9bd98bc8"}
{"at":"2026-05-22T14:30:11+00:00","actor":"agent","kind":"submission.received","summary":"Commercial section submitted by bob@partner.com","ref":"p-9bd98bc8"}
{"at":"2026-05-23T08:02:44+00:00","actor":"agent","kind":"task.overdue","summary":"PM Scope — due 2026-05-22, no submission received","ref":"p-9bd98bc8"}
```

---

## 7. Foundry Toolbox — one MCP endpoint for all of M365

The **Charter-Agent-Tools Toolbox** is a Foundry-managed MCP gateway that aggregates 8 WorkIQ M365 Intelligence servers into a single MCP endpoint. The agent accesses all 135 tools through one `MCPStreamableHTTPTool` instance.

### What the Toolbox provides

| WorkIQ server | Capabilities |
|---|---|
| WorkIQMail | Read mailbox, send emails, find attachments |
| WorkIQCalendar | List events, read meeting details, find transcripts |
| WorkIQTeams | Send DMs, read chat threads, search channels |
| WorkIQFiles / WorkIQSharePoint | Browse folders, read documents, create files |
| WorkIQOneDrive | Upload/download files, share links |
| WorkIQWord | Create, edit, and read Word documents |
| WorkIQUser | Resolve UPNs, look up Entra Object IDs |
| WorkIQCopilot | Natural-language search across the user's M365 estate |

### Why one endpoint matters

Before the Toolbox, an agent would need to discover, authenticate to, and maintain connections with each MCP server independently. The Toolbox collapses this into:
1. One MCP connection, one `tools/list` call
2. Tool names are namespaced (`WorkIQMail2___SendEmailWithAttachments`) — the agent doesn't need to know which server each tool belongs to
3. Authentication to WorkIQ is handled by Foundry — the agent never holds WorkIQ credentials

### Toolbox authentication (two separate credentials)

There are two distinct auth concerns, often confused:

| Auth concern | Who handles it | Credential |
|---|---|---|
| **Reaching the Toolbox endpoint** (HTTP to the MCP gateway) | Agent code | Agent's Managed Identity (prod) or `az login` (local) — scope `https://ai.azure.com/.default` |
| **Acting as the user inside WorkIQ** (which M365 data is accessed) | Foundry platform | User's bearer token, forwarded from the `/responses` call |

The agent's Managed Identity only gets it to the door. What it can do inside — whose emails it reads, whose calendar it sees — is entirely determined by the **user bearer token** the desktop client attached to the `/responses` request. The Foundry platform performs this substitution invisibly; the agent code sees only successful or failed tool results.

### OBO and the consent flow

WorkIQ MCP connections are marked for **OAuth Identity Passthrough** in the Foundry portal. On the first call per (user, WorkIQ connection):

1. Foundry pauses the response stream
2. It emits an `oauth_consent_request` SSE event containing a Microsoft login URL
3. The desktop client opens that URL in a browser and the user grants consent once
4. The client retries with `previous_response_id` — Foundry resumes the same turn
5. From that point on, every MCP call for that user and that connection succeeds silently

The agent process is completely uninvolved in this flow. It does not store tokens, does not run OBO, and does not know consent happened. The retry just works.

### Local development and the Toolbox

When running locally (`python -m charter_agent`), the agent process is local but:
- The **Foundry model** (`gpt-5.x`) is remote — every model invocation goes to Azure
- The **Toolbox** is remote — every WorkIQ tool call goes to the Foundry MCP gateway
- The **auth** uses your `az login` identity instead of Managed Identity — same `https://ai.azure.com/.default` scope

There is no local mock of the Toolbox or the model. Local runs are for iterating on skill logic, state management, and agent plumbing with real cloud endpoints. The only difference from production is the identity used.

---

## 8. Identity and authentication

See [`architecture/architecture_and_design.md §7`](../architecture/architecture_and_design.md#7-identity--auth) for the complete flow.

In brief:
- **Agent → Foundry model**: Managed Identity (production) / `az login` (local). Injected by the platform or by `DefaultAzureCredential`.
- **Agent → Toolbox endpoint**: Same credential. Set via `_ToolboxAuth` in `foundry_host.py`, which injects a fresh bearer on every MCP HTTP request.
- **User → WorkIQ via Toolbox**: The desktop client attaches the user's bearer to `/responses`. Foundry extracts it and substitutes it into Toolbox MCP calls marked for Identity Passthrough. **Zero agent code involved.**

The agent runs no OBO flow, holds no WorkIQ refresh tokens, and stores no client secrets.

---

## 9. Telemetry and observability

Two decoupled layers:

### Platform telemetry (Foundry SDK)

`ResponsesHostServer` auto-instruments every request with App Insights / OpenTelemetry spans once the hosting environment is connected. Spans cover:
- Root span per `/responses` call
- Model invocation spans (token counts, latency)
- Tool call spans (tool name, duration, success/failure)

At boot, `ProcessAttributesSpanProcessor` stamps every span with process-wide attributes (`project.id`, conversation ID) so all spans for a request are correlated in App Insights.

Note: `AIProjectInstrumentor().instrument()` is deliberately **not called** — as of the current SDK version it wraps the SSE stream in a class that breaks the streaming consumer. The Foundry runtime's own instrumentation provides equivalent coverage.

Monitoring in Azure Portal:
- Application Insights → Traces → filter by `project.id` or `agent_session_id`
- Live Metrics for real-time request counts and failure rates
- Failures blade for tool call errors and model failures

### Structured activity log (`activity.json`)

Every significant workflow event is appended to `$HOME/projects/<pid>/activity.json` via `observability.log_activity()`:

```python
log_activity(actor="agent", kind="kickoff.sent", summary="Teams DM → alice@contoso.com")
log_activity(actor="host",  kind="project.switch", summary="active project = p-9bd98bc8")
```

This log is:
- **Not telemetry** — it is product behaviour, part of the project record
- Read by the desktop client and rendered in the activity stream panel
- Read by the agent on resume to recover workflow context
- Exportable to SharePoint at project close as the audit trail

---

## 10. Local development

### Prerequisites

- Python 3.12+
- `az login --tenant <your-tenant>` (active session required for Toolbox auth)
- A `.env` file with the Foundry project settings (see `.env.example`)

### Setup (one-time)

```powershell
cd agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### Configuration

```powershell
Copy-Item .env.example .env
# Edit .env:
#   AZURE_AI_MODEL_DEPLOYMENT_NAME = gpt-5.4  (or your deployment)
#   FOUNDRY_PROJECT_ENDPOINT = https://ocvp-agent-svc-resource.services.ai.azure.com
#   TOOLBOX_NAME = Charter-Agent-Tools
```

### Running the agent

```powershell
.\.venv\Scripts\Activate.ps1
python -m charter_agent
# Logs: INFO charter_agent starting Responses protocol server
# INFO AgentServerHost starting on 0.0.0.0:8088
```

The server listens on `http://localhost:8088/responses`. It is using the real Foundry model and real Toolbox — every call goes to Azure. Local state persists to `agent/.charter-agent-home/`.

### Diagnostic probes (no HTTP server)

```powershell
# What skills are loaded
python scripts/dev_run.py skills

# What tools the Toolbox exposes
python scripts/dev_run.py list-tools

# One full calendar query end-to-end
python scripts/smoke_calendar.py

# Test the Responses surface directly
python scripts/smoke_responses.py "list your skills"
python scripts/smoke_responses.py "..." --previous-response-id <id>
```

### Local state sandbox

While running locally, all state writes go to:
```
agent/.charter-agent-home/
├── .active_project
└── projects/
    └── p-<id>/
        ├── project_charter.md
        ├── project_log.json
        └── activity.json
```

State persists across local restarts. Delete `.charter-agent-home/` to start completely fresh.

---

## 11. Deploying to Foundry

### Build and push the container image

```powershell
# From repo root — ACR remote build (handles linux/amd64 on any host arch)
az acr build `
  --registry pcdotaiagentd10b5a `
  --image charter-agent:v2 `
  --file agent/Dockerfile `
  .
```

### Register the agent version

```powershell
cd agent
python scripts/deploy.py
```

This registers a new agent version in the Foundry project, pointing at the new image tag. The Foundry runtime handles blue-green deployment — existing sessions continue on the old version until they naturally expire or are migrated.

### Environment variables in Foundry

Set via the Foundry portal or `azd env set`:

| Variable | Purpose |
|---|---|
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Model deployment (e.g. `gpt-5.4`) |
| `FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint URL |
| `TOOLBOX_NAME` | Toolbox name (e.g. `Charter-Agent-Tools`) |
| `TOOLBOX_VERSION` | Toolbox version (omit for consumer/prod endpoint) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Auto-set by Foundry when App Insights is linked |

In production, the Managed Identity is injected by the platform — no credential env vars needed.

---

## 12. Testing

```powershell
cd agent
.\.venv\Scripts\Activate.ps1
python -m pytest -q
# 23 tests, all pure-Python, no network required
```

Tests cover: `state.py` path containment and atomic writes, `skill_loader.py` manifest parsing, `state_tools.py` tool wrappers, `project_router.py` preamble parsing, and `observability.py` log appending. Every test that would touch WorkIQ or Foundry mocks the boundary with `respx`.

---

## 13. Module reference

| Module | Responsibility |
|---|---|
| `__main__.py` | Boot: assert env vars, enable OTel, call `bootstrap()`, start `ResponsesHostServer` |
| `state.py` | All `$HOME` I/O — path validation, atomic writes, active project pointer |
| `observability.py` | OTel span processor; `log_activity()` → `activity.json` |
| `runtime/foundry_host.py` | Sole owner of `FoundryChatClient`, `MCPStreamableHTTPTool`, warm per-skill `Agent` instances |
| `runtime/responses_host.py` | `ResponsesHostServer` subclass — pre-routes requests, swaps skill Agent, degrades history failures |
| `runtime/project_router.py` | Parses `[charter-agent-context:]` preamble, calls `state.set_active_project_id()` |
| `runtime/skill_loader.py` | Reads `skills/*/SKILL.md`, validates frontmatter, returns `SkillBundle` per skill |
| `runtime/state_tools.py` | MAF `@tool` wrappers: `state_read_text/json`, `state_write_text/json`, `state_list_files`, `log_workflow_step` |
| `skills/sow-response/tools.py` | Skill-specific in-process tools: `load_project_state`, `start_charter`, `add_charter_task`, `record_submission`, `dashboard_payload`, `publish_view`, etc. |
| `skills/sow-response/SKILL.md` | The agent's instructions for the SOW response workflow — **authoritative contract** |
| `skills/general/SKILL.md` | Default skill for routing and general queries |
