# Project Workspace — Implementation Specification

> An agent-orchestrated project coordination workspace built on Microsoft Foundry hosted agents, WorkIQ, GHCP Copilot SDK, and Azure Container Apps. Generic enough to handle any cross-functional project; specific enough to feel bespoke per project.

---

## 1. Purpose of this document

This spec is the implementation contract for a coding agent (GHCP) to build the system end-to-end. It captures the use case, the architecture, the technology choices and why each was made, the generic project model, the per-project customisation pattern, and the implementation guidance the coding agent should keep in mind while building.

Read sections 2–4 to understand *what* you're building and *why*. Read sections 5–8 to understand *how* the pieces fit. Read section 9 for the build order. Read section 10 for the things that are easy to get wrong.

---

## 2. The problem this solves

### 2.1 The shape of work being replaced

Cross-functional projects inside large organisations follow a recognisable pattern: a senior person (Chief of Staff, programme manager, deal lead, audit coordinator) is responsible for delivering a consolidated artifact by a deadline. The substance of the artifact comes from several other senior people, each contributing a piece. The coordinator's actual job is:

- Decomposing the deliverable into sections/tasks and assigning each to the right owner
- Communicating expectations clearly across email, Teams, calendar invites, task lists
- Watching for delivery across heterogeneous channels (people deliver however they prefer — email attachment, OneDrive link, Teams message, SharePoint upload, sometimes typed inline)
- Reading what comes in and judging whether it meets the runbook's expectations
- Nudging gently when things are late, intelligently when people are unavailable
- Flagging gaps and inconsistencies
- Consolidating everything into the final deliverable
- Keeping all stakeholders informed of status without holding constant status meetings

Today this is done with a frantic SharePoint folder, an Excel tracker, a dozen Teams threads, and the coordinator's own mental model. It's exhausting, error-prone, and the coordinator's time is mostly spent on coordination overhead rather than judgement.

### 2.2 What this system does instead

The coordinator interacts with a single Foundry-hosted agent. The agent runs the coordination — sets up the structure, watches for deliveries across all channels, reads what arrives, checks against the runbook, flags gaps, drafts suggested follow-ups for the coordinator's approval, and consolidates the final artifact. A live web URL lets every stakeholder see the project's status in real time without asking for it. When the project completes, the workspace dissolves.

The coordinator's job becomes: define the project, ratify the agent's plan, make the judgement calls the agent surfaces, approve outbound communications. The agent does the rest.

### 2.3 Concrete example — Monthly board pack

Used throughout this spec as the canonical scenario, but the architecture is generic.

A Chief of Staff opens the workspace and says: *"Assemble the November board pack — same structure as last month."* The agent reads last month's pack and the firm's runbook from SharePoint, proposes a 4-task plan (Financial Summary / Delivery Health / Top Clients / Talent & Attrition, each with an owner, deadline, and section-specific requirements), gets her confirmation, and fans out the kickoff — creating templated drafts in SharePoint, posting tagged Teams messages, sending individual briefing emails, creating Outlook Tasks.

Over the following week, owners deliver however they prefer. The agent captures each delivery, reads it via WorkIQ, surfaces highlights and runbook gaps on a live dashboard the team shares, drafts nudges for the coordinator's approval when people are late, handles reassignments cleanly when someone is unavailable. When all sections are in, it consolidates the Word doc and flags a $1.2M reconciliation gap it caught between two sections. The board pack goes to the meeting; the working state dissolves.

### 2.4 Other scenarios the same architecture handles

The system is built to be project-agnostic. The same code, with no changes, runs:

- Quarterly budget consolidation across regional teams
- Audit response coordination across business units
- All-hands or town-hall deck assembly across leadership
- Customer escalation recovery plan across the delivery account team
- M&A diligence response packs across functional leads
- Regulatory inquiry responses across legal, compliance, and product
- New-hire onboarding plan execution across hiring manager, HR, IT, facilities

What changes between these is the *content of the project's charter* (section 6), not the system's behaviour.

---

## 3. Architecture overview

### 3.1 The four components

```
Browser  ──HTTPS──►  Azure Container App (or SWA)  ──Foundry API──►  Foundry Hosted Agent
                    │  (generic, multi-tenant,                       │  (one deployment;
                    │   stateless, ~50 LOC shell)                    │   per-project sessions)
                    │                                                │
                    │  Entra SSO; reads /p/{project_id}              │  ┌──────────────────┐
                    │  from URL; passes as                           ──┤ Session sandbox  │
                    │  x-ms-chat-isolation-key                         │ ($HOME persists, │
                                                                       │  Charter,        │
                                                                       │  state,          │
                                                                       │  Copilot-gen     │
                                                                       │  code)           │
                                                                       └──────────────────┘
                                                                              │   │
                                                                              │   ▼
                                                                              │  GHCP Copilot SDK
                                                                              │  (the agent runtime;
                                                                              │   one warm Copilot
                                                                              │   session per Foundry
                                                                              │   session)
                                                                              ▼
                                                                          WorkIQ MCP servers
                                                                          (called in visiting
                                                                           user's OBO context;
                                                                           reads M365 data)
```

### 3.2 Why each component, and what it does

**Container App (or Static Web App) — generic frontend.** One deployment per tenant. Knows nothing about specific projects. Reads the project ID from the URL path (`/p/{project_id}`). Authenticates the visiting user via Entra SSO. Calls the Foundry agent's `/invocations` endpoint with the project ID as `x-ms-chat-isolation-key`. Renders whatever the agent returns. Scales horizontally like any stateless web app. Never instantiated per project.

**Foundry hosted agent — the workspace's brain.** One deployed agent. Each project is a separate session in this agent, keyed by chat-isolation key (the project ID). State and per-project code live in `$HOME` of each session's microVM sandbox. Sessions persist up to 30 days with 15-minute idle timeout (compute deprovisions, $HOME state is preserved, resume is automatic on next request). The agent's code is project-shape-agnostic — it operates against a Project Charter (section 6).

**WorkIQ MCP servers — the M365 data plane.** Used for reading email, Teams chats, SharePoint, OneDrive, Calendar, and for sending Teams messages and emails on a user's behalf. Critically: **WorkIQ uses delegated user authentication only — there is no application-only mode**. Every WorkIQ call runs in the OBO context of the currently-visiting user. The architecture must respect this.

**GHCP Copilot SDK — the agent's runtime.** Not a side-process for occasional code generation; *the* runtime the agent is built on. Every `POST /invocations` is forwarded into a Copilot session keyed by the Foundry session ID; the session is kept warm across requests and provides the agent's conversational memory automatically. The agent's project-workspace behaviour is shaped declaratively by **Copilot SDK skills** auto-loaded from `agent/skills/*/SKILL.md`. Only when a project genuinely needs deterministic running code (template-specific Word stitching, cross-section numeric reconciliation in the consolidator) does the agent ask the same Copilot session to generate a Python module into `$HOME/code/` — that path is exceptional, not the norm. See §4.3 for the runtime details.

### 3.3 The key architectural separation

> **The agent's behaviour is generic. The project's structure is specific. The bridge is the Project Charter — a structured artifact captured at kickoff and treated as the project's constitution.**

This separation is what makes the system handle any project shape without per-project deployment. The agent never has to know what a "board pack" or "audit response" specifically means. It knows how to act on a Charter. The Charter is what carries the variety.

### 3.4 What is explicitly NOT in this architecture

These are deliberate exclusions; the coding agent should not introduce them.

- **No cron jobs, no scheduled tasks, no background workers.** The agent only acts when a user invokes it. This is non-negotiable — WorkIQ cannot run on a service identity, so autonomous background activity would lose the user context that makes WorkIQ work at all.
- **No per-project Container App or per-project deployment.** Per-project specificity comes from session state and Copilot-generated code inside the sandbox, not from infrastructure.
- **No project database, no message queue, no event bus.** $HOME in the session sandbox is the project's state store. The Charter is the schema. Don't introduce external state unless explicitly required.
- **No exposing arbitrary ports from the sandbox.** Foundry hosted agents only serve `/responses` and `/invocations`. The frontend is served from the Container App; it calls the agent for content.
- **No autonomous outbound communications.** Every Teams message, email, or task sent to a stakeholder must be human-approved by the coordinator. The agent drafts; the human approves; the agent sends in the human's OBO context.

---

## 4. Technology stack — detailed

### 4.1 Microsoft Foundry hosted agents

The agent is deployed once as a container image to Azure Container Registry, then registered with Foundry Agent Service. The platform provisions per-session microVM sandboxes on demand and handles lifecycle.

Key properties to rely on:

- **Per-session sandbox isolation.** Hypervisor-isolated microVM per session. Each session gets its own `$HOME` directory that persists across idle/resume cycles. No cross-session access.
- **Session lifetime up to 30 days, 15-minute idle timeout.** Compute deprovisions on idle; state is preserved; resume is automatic on next request. Cold-start latency is acceptable for human-driven interaction (single-digit seconds).
- **Isolation keys for multi-user access.** `x-ms-user-isolation-key` is set automatically from the caller's Entra token. `x-ms-chat-isolation-key` is set explicitly by the client to scope sessions to a logical conversation/workspace. Multiple users hitting the same chat-isolation-key see and modify the same session — this is the primitive that makes shared project workspaces possible.
- **Two protocols available: Responses and Invocations.** Use **Invocations** for this system. Responses comes with platform-managed conversation history that we don't want (state lives in our Charter + state files in $HOME, not in chat history). Invocations gives us full control over the request/response shape and state management.
- **OBO identity passthrough at the tool layer.** When the agent calls MCP tools that are configured with OAuth identity passthrough, Foundry exchanges the caller's token for a downstream token. This is what lets WorkIQ run in the visiting user's context.
- **Agent identity and observability.** Each agent gets a dedicated Entra Agent ID. Application Insights connection string is injected automatically; OpenTelemetry tracing works out of the box. Use this for the audit log.

The agent runs the **GitHub Copilot SDK** ([`github-copilot-sdk`](https://pypi.org/project/github-copilot-sdk/)) directly on top of the **Invocations protocol server** ([`azure-ai-agentserver-invocations`](https://pypi.org/project/azure-ai-agentserver-invocations/)). The canonical reference is the Microsoft sample at `microsoft-foundry/foundry-samples/samples/python/hosted-agents/bring-your-own/invocations/github-copilot`. We intentionally do **not** wrap this in Microsoft Agent Framework — see [AGENTS.md §3 invariant 11](../AGENTS.md) and §4.3 below for the rationale and shape.

### 4.2 WorkIQ MCP servers

Configured as MCP connections directly in the Foundry project (via the portal or `azd`). The servers expose tools for: Mail, Calendar, Files (SharePoint/OneDrive), Teams, plus a general `workiq.ask` natural-language query.

Critical constraints when using Foundry hosted agents (which we are):

- **Delegated permissions only — no application-only mode.** Every WorkIQ call runs as the currently-signed-in user, via OAuth Identity Passthrough on the Foundry MCP connection.
- **Microsoft 365 Copilot license required** for every user who interacts with the system (coordinator, owners, observers).
- **One-time tenant admin setup** — registering Agent 365 service principals (the `Enable-WorkIQToolsForTenant.ps1` step) — must already be done. If WorkIQ is already callable from a Foundry portal agent in your tenant, this is confirmed and no further admin action is needed.
- **No additional app registration needed for WorkIQ itself.** The Foundry project's WorkIQ MCP connections handle the platform-level consent and token exchange. You do not need to register your own app with WorkIQ scopes — those scopes are pre-granted at the Foundry/Agent 365 platform level.
- **OAuth Identity Passthrough must be enabled on the MCP connections.** Verify in the Foundry portal that each WorkIQ MCP connection has identity passthrough turned on. If the portal playground returns *your* data when you call WorkIQ tools, passthrough is working. Without it, calls would run as the agent identity and either fail (no M365 license) or return wrong data.
- **An app registration IS needed for the Container App frontend's user sign-in**, but only with basic scopes like `User.Read`. Prefer reusing an existing internal SSO app registration in your tenant — most large orgs have one. Alternative: publish the agent to Teams (section 4.4 alternative) and skip the custom frontend entirely, eliminating the need for any new registration.

Net: if you can already see a Foundry-portal agent making WorkIQ Mail/Calendar/Teams calls successfully, the tenant-level setup is sorted. The remaining identity work is only for your frontend's user sign-in, and that can be handled with a basic-scope reused registration or by publishing to Teams.

### 4.3 GHCP Copilot SDK as the agent's runtime

This is the canonical pattern Microsoft demonstrates in their official sample at `microsoft-foundry/foundry-samples/samples/python/hosted-agents/bring-your-own/invocations/github-copilot`. Worth treating as the reference implementation.

**The fundamental inversion to grasp.** Copilot SDK is not a code-writing subprocess the main agent occasionally invokes. Copilot SDK *is* the agent's runtime. Every `POST /invocations` from the frontend is forwarded into a Copilot session; every Copilot session event streams back as SSE. The "agent" is a thin shell around a Copilot SDK client.

**The Foundry session and the Copilot session map 1:1**, both keyed by `FOUNDRY_AGENT_SESSION_ID`. Copilot's persistent sessions with intelligent compaction become the agent's conversational memory automatically. You don't write multi-turn state management — the SDK handles it.

**Authentication — we use the GitHub PAT route.** A fine-grained GitHub PAT (`github_pat_…`, `gho_…`, or `ghu_…`; classic `ghp_` tokens are **not** supported) with the *Copilot Requests → Read-only* permission, owned by a service account (not an individual). Set via `azd env set GITHUB_TOKEN=…`, injected into the hosted agent's environment from Key Vault by `agent.yaml` / the Bicep template. The SDK runs on GHCP's default model (currently Claude Opus 4.7) — the same model GHCP uses in VS Code.

**Do not set `AZURE_AI_MODEL_DEPLOYMENT_NAME` on the agent process.** If both `GITHUB_TOKEN` and `AZURE_AI_MODEL_DEPLOYMENT_NAME` are configured, the Copilot SDK silently flips to a Foundry-model backend ([sample README](https://github.com/microsoft-foundry/foundry-samples/blob/main/samples/python/hosted-agents/bring-your-own/invocations/github-copilot/README.md): *"If both are set → the Foundry model takes precedence"*) and our model surface is swapped without any compile-time error. The boot sequence asserts the env-var policy and refuses to start otherwise. See [AGENTS.md §4.2](../AGENTS.md#42-model-assignment-policy). The Foundry-deployed `gpt-5.x` models remain reachable as **named Copilot tools** if a skill explicitly opts in, authenticated via the agent's Managed Identity — but they are not the agent's runtime.

**The CLI is an implementation detail, not something you invoke yourself.** `CopilotClient.start()` manages the CLI's lifecycle as part of the SDK. From your code's perspective you're using a clean Python (or .NET) client API — `client.start()`, create session, send prompt, receive events. The JSON-RPC plumbing is hidden.

**Skills shape the agent's behaviour declaratively.** Any subdirectory under `skills/` with a `SKILL.md` file is auto-loaded by Copilot SDK. The SKILL.md contains instructions for when and how to behave when that skill is "active." This is how you give the agent its project-workspace specialisation — see section 7 for the skills structure recommended for this system. Skills are far more transparent and auditable than dynamically generated `.py` modules; default to skills, fall back to module generation only when project-specific code (e.g. a per-deliverable consolidator) genuinely warrants it.

**Sample skeleton:**

```python
# main.py — the hosted agent
from copilot_sdk import CopilotClient
from azure_ai_agentserver_invocations import InvocationAgentServerHost

client = CopilotClient()
await client.start()

app = InvocationAgentServerHost()
_sessions: dict[str, "CopilotSession"] = {}

@app.invoke_handler
async def handle_invoke(request):
    session_id = os.environ["FOUNDRY_AGENT_SESSION_ID"]
    if session_id not in _sessions:
        _sessions[session_id] = await client.resume_or_create_session(session_id)
    data = await request.json()
    async for event in _sessions[session_id].send(data["input"]):
        yield f"data: {json.dumps(event.to_dict())}\n\n"
    yield "event: done\n\n"
```

**Deployment.** Use `azd deploy` (NOT local `docker build` unless you force `--platform=linux/amd64`). The Foundry service requires `linux/amd64` images, which is the issue Apple Silicon developers will hit immediately. ACR remote build via `azd` produces correct images.

### 4.3.1 When to still generate `.py` modules via Copilot

The skills-driven runtime handles most behaviour declaratively. But there are cases where the agent genuinely needs to *produce running code* — typically the final consolidation step where format-specific stitching logic is required (assemble these four Word section files into one master Word doc, with cross-references and reconciliation). For those, the agent can ask Copilot via the session to write code into `$HOME/code/` and import it on demand. Keep this exceptional, not routine — skills should carry as much behaviour as possible.

### 4.4 Azure Container Apps (frontend)

A single Container App, deployed once per tenant. Suggested stack: Python + FastAPI for the backend-for-frontend, plus a small React or vanilla-JS SPA for the UI shell. Alternative: Azure Static Web App with an Azure Functions backend if you prefer that model. Either is fine; the Container App pattern is slightly simpler operationally.

Responsibilities:

- Entra SSO authentication (use MSAL).
- URL routing: `/p/{project_id}` maps the path segment to the chat-isolation key.
- Per-request: acquire the visiting user's token, call the Foundry agent's `/invocations` endpoint with the chat-isolation key header.
- Render the agent's structured response into the dashboard view.
- Forward user actions (approve nudge, reassign task, amend charter) as `/invocations` calls.

The frontend does NOT hold project state. State lives in the agent's sandbox. The frontend is a renderer and a request gateway.

### 4.5 Azure Entra ID

App registrations needed:

- **The agent's identity** (created automatically by Foundry at deployment).
- **The frontend's app registration** for user sign-in (MSAL). Configure for SPA + web depending on your frontend split.
- **WorkIQ-related permissions** on the app registration that the frontend uses to acquire tokens with delegated WorkIQ scopes.

Conditional Access: agent identities and user OBO flows are both subject to Conditional Access policies. Plan for this.

### 4.6 SharePoint / Microsoft 365

The system reads from and writes to whatever SharePoint sites the coordinator's project lives in. No special site setup is required beyond standard M365 governance — the agent creates the project folder, sets up templated draft files, and exports the final deliverable. Permissions on those files are managed via standard Graph API calls (the agent shares each templated draft with its assigned owner; revokes access on reassignment).

### 4.7 Observability and audit

- Application Insights connection string is auto-injected into the agent container. Use the default OpenTelemetry tracing.
- Every action the agent takes is logged to the state file in `$HOME` (the activity stream) AND emitted as a structured trace.
- The Charter + activity stream together form a recoverable audit trail of any project.

---

## 5. The user experience

### 5.1 Roles

- **Project owner / coordinator** — initiates the project, ratifies the Charter, makes judgement calls, approves outbound communications. The Chief of Staff in the canonical example.
- **Task owners** — the stakeholders responsible for delivering each task's content. Interact mostly through their normal M365 channels (email, Teams, files); may optionally check the dashboard.
- **Observers** — anyone with view access to the dashboard URL who isn't producing content. Other prep team members, an executive sponsor, etc.

### 5.2 The shared dashboard URL

Every project has one URL: `https://workspace.{your-domain}.com/p/{project_id}`. The project ID is generated at kickoff and is also the chat-isolation key for the Foundry session.

Anyone in the organisation with the URL and a valid Entra account can open it. On first load, the frontend:

1. Authenticates the user via Entra SSO.
2. Calls the Foundry agent's `/invocations` endpoint with the project ID as `x-ms-chat-isolation-key` and action `render_dashboard`.
3. The agent wakes up (or is already warm), runs a refresh cycle in the visiting user's OBO context (reading their visible signals via WorkIQ), updates the state file, returns the dashboard payload.
4. The frontend renders.

This is the only mechanism by which the agent "runs." There is no background polling, no scheduled wake-up. The system is fully responsive.

### 5.3 The coordinator's interactions

The coordinator has all the rendering the observers have, plus:

- A chat input on the dashboard that sends free-text instructions to the agent (`"reassign Talent to Sofia"`, `"add a vendor-risk section to the runbook"`, `"draft the wrap-up note for the team"`).
- An exceptions panel where suggested actions await approval (`"Suggest nudging Marcus about missing NPS metric — click to send"`).
- An "amend Charter" affordance for changing the project mid-flight.

### 5.4 What task owners see

The same dashboard, with the same project state. They cannot approve actions; they can see their own tile, the runbook expectations for their section, and the live status. They deliver their content via whatever channel they prefer; the agent finds it.

### 5.5 The asymmetric data visibility

A subtle but important point: when a task owner visits the dashboard, the refresh runs in *their* OBO context. WorkIQ will only return content *they* can see. So an owner's visit refreshes shared-visibility data (status of shared files, public Teams channel activity) but cannot enrich with cross-stakeholder data they don't have access to.

The coordinator's visits, by contrast, run in *her* context — which typically has broader visibility — so her visits do full enrichment. The agent caches the most recent enriched state and serves it on subsequent visits. The dashboard displays a "last enriched at HH:MM by Anita Rao" timestamp so everyone knows the data freshness.

Do not try to fix this asymmetry by using a service identity for WorkIQ — it's not supported. Embrace it; it's actually a good privacy property.

---

## 6. The Project Charter — the system's most important artifact

### 6.1 What the Charter is

A single JSON file in `$HOME/charter.json` that defines everything the project consists of. It is written by the agent at kickoff, ratified by the coordinator before any action is taken, and locked thereafter (amendments require explicit coordinator action).

Every other piece of the system reads from the Charter to know how to behave. The Charter is the project's constitution.

### 6.2 Schema (canonical)

```json
{
  "project_id": "board-nov-2025",
  "project_kind": "board_pack",
  "version": 1,
  "ratified_at": "2025-11-14T09:14:00Z",
  "ratified_by": "chief.of.staff@firm.com",

  "goal": "Assemble November 2025 board pack for the 25 Nov board meeting",

  "deliverable": {
    "type": "consolidated_document",
    "format": "docx",
    "template_path": "sharepoint:Templates/BoardPack_Template.docx",
    "due": "2025-11-22T17:00:00Z",
    "output_location": "sharepoint:Board-Nov-2025/Final/"
  },

  "tasks": [
    {
      "task_id": "fin",
      "title": "Financial Summary",
      "description": "Revenue and margin summary with month-over-month comparison",
      "owner": {
        "name": "Anita Rao",
        "email": "anita.rao@firm.com",
        "upn": "anita.rao@firm.com",
        "role": "CFO"
      },
      "due": "2025-11-20T17:00:00Z",
      "expected_artifact_type": "excel",
      "runbook_requirements": [
        {"id": "rev_mom", "description": "Revenue with month-over-month variance"},
        {"id": "margin_mom", "description": "Margin with month-over-month variance"},
        {"id": "top5_total", "description": "Top-5 client revenue total (reconciles with Top Clients section)"}
      ],
      "watch_channels": [
        {"kind": "sharepoint_file", "path": "Board-Nov-2025/Financial_Summary_DRAFT.xlsx"},
        {"kind": "email_from", "address": "anita.rao@firm.com"},
        {"kind": "teams_thread", "channel": "#board-prep", "from": "anita.rao@firm.com"},
        {"kind": "onedrive_share", "user": "anita.rao@firm.com"}
      ],
      "templated_file_path": "sharepoint:Board-Nov-2025/Financial_Summary_DRAFT.xlsx"
    }
    // ... three more tasks
  ],

  "consolidation_rules": [
    {
      "id": "top5_reconcile",
      "description": "Top-5 client revenue in Financial Summary must match Top Clients section total",
      "rule_kind": "cross_section_numeric_match",
      "sources": ["fin.top5_total", "cli.section_total"]
    }
  ],

  "communication": {
    "kickoff_teams_channel": "teams:#board-prep",
    "project_email_alias": "board-prep@firm.com",
    "kickoff_message_template": "default"
  },

  "stakeholders": {
    "coordinator": "chief.of.staff@firm.com",
    "approvers": ["ceo@firm.com"],
    "observers": ["board-prep-team@firm.com"]
  },

  "consolidator_module_path": "$HOME/code/consolidator.py"
}
```

### 6.3 How the Charter is created

The kickoff flow:

1. **Coordinator types a kickoff prompt** in natural language (or selects a template). E.g. `"Assemble the November board pack — same structure as last month."`

2. **Agent grounds the prompt in prior context.** Calls WorkIQ to find the most similar prior artifact (last month's board pack) and any project runbook (`Board Pack Runbook.docx`). Reads them.

3. **Agent proposes a Charter** populated from prior context. Surfaces it to the coordinator in a structured view: tasks, owners, deadlines, runbook requirements, consolidation rules.

4. **Coordinator reviews and ratifies, or amends.** *"Yes, but deadline is Day 4 not Day 5 — board meeting moved up."* Agent applies the amendment, re-shows, coordinator confirms.

5. **Charter is locked.** Written to `$HOME/charter.json`. Any later changes require an explicit `amend_charter` action that re-runs the ratification step.

6. **Kickoff actions execute against the locked Charter.** Create the SharePoint folder. For each task: create the templated draft file with appropriate permissions, send the briefing email, post the tagged Teams kickoff message, create the Outlook Task. Persist what was done to the activity stream.

7. **Per-project code is generated via Copilot SDK — but only when needed.** With the Charter as input, the agent's `copilot_runtime` asks Copilot to write `consolidator.py` into `$HOME/code/` *if* the project's consolidation rules genuinely need deterministic Python (template-specific Word/Excel stitching, cross-section numeric reconciliation). Renderer and compliance behaviour are skills (§7), not generated modules. The generated `consolidator.py` is imported on subsequent invocations.

### 6.4 Why this design works for ANY project shape

The Charter schema captures what every cross-functional project has in common: a goal, a deliverable, tasks with owners and deadlines, channels to watch, requirements to check, rules to apply. Different project shapes simply produce different *contents* in this same schema.

- A budget consolidation has tasks per region, each producing a populated budget template, with consolidation rules around rolling up cost centres.
- An audit response has tasks per finding, each requiring an evidence package, with rules around evidence completeness.
- An all-hands deck has tasks per leader, each producing slides, with lighter compliance and a faster cadence.

The agent's code is identical across all of these. What varies is the Charter's content and the Copilot-generated modules derived from it.

### 6.5 Charter amendment

When the coordinator says `"add a vendor-risk section, owned by Marcus"` mid-project, the agent:

1. Validates the amendment (no orphan dependencies, no conflicts with completed work).
2. Increments `version` and updates `charter.json`.
3. Re-runs Copilot SDK to regenerate `consolidator.py` if the changes affect its inputs (added/removed tasks, changed templates, changed `consolidation_rules`). Renderer and compliance behaviour come from skills and need no regeneration.
4. Fans out the new task's kickoff actions (templated file, briefing, task).
5. Logs the amendment to the activity stream with the coordinator's identity.

The Charter is versioned for audit purposes.

---

## 7. Project-specific behaviour — skills and (selectively) generated modules

The agent's specialisation for any given project comes from two sources, applied in this order of preference:

1. **Declarative skills** in the `skills/` directory, auto-loaded by Copilot SDK. These shape the agent's reasoning declaratively — *"when doing X, behave like this, considering these factors."* This is the primary mechanism.
2. **Copilot-generated Python modules** in `$HOME/code/`, written at kickoff against the Charter, used only when project-specific *running code* is genuinely required.

Default to skills. Reach for module generation only when the work cannot be expressed as instructions to the model.

### 7.1 The recommended skills structure

Built into the agent image, present across every project:

```
skills/
├── project-kickoff/
│   └── SKILL.md       # how to read prior context, propose a Charter, run ratification
├── status-refresh/
│   └── SKILL.md       # how to run the per-invocation refresh: poll, classify, update
├── capture-classify/
│   └── SKILL.md       # how to classify a candidate event (submission/question/etc.)
├── compliance-check/
│   └── SKILL.md       # how to evaluate a submission against runbook_requirements
├── draft-outbound/
│   └── SKILL.md       # how to draft Teams/email messages for coordinator approval
├── consolidate/
│   └── SKILL.md       # how to assemble the deliverable; when to invoke a module
└── amend-charter/
    └── SKILL.md       # how to handle Charter amendments mid-project
```

Each SKILL.md contains:

- A clear description of when the skill is active (the triggering condition).
- The reasoning steps the agent should walk through.
- The Charter fields it should consult.
- The expected output shape (JSON, Word doc, Teams card payload, etc.).
- Boundaries — what the agent must NOT do without human approval.

These skills travel with the agent image. They are the same across every project. The *content* the agent reasons about (the Charter, the state, the captured submissions) is what makes each project specific.

### 7.2 Example skill — `compliance-check/SKILL.md`

```markdown
---
name: compliance-check
description: Evaluate a captured submission against the runbook requirements
  for its task. Returns a structured list of met / gap / ambiguous findings.
---

# Compliance check

You are evaluating a submission that has arrived for a task in the active Project Charter.

## Inputs available to you

- `submission` — content_type (excel/word/ppt/text), extracted_content (from WorkIQ),
  metadata (sender, channel, timestamp)
- `task` — the matching task entry from charter.tasks
- `task.runbook_requirements` — the list of requirement objects to check against

## What to do

For each requirement in `task.runbook_requirements`:

1. Read the requirement description carefully.
2. Search the extracted content for evidence the requirement is satisfied.
3. Return one of three statuses:
   - **met** — clear evidence in the submission satisfies the requirement.
   - **gap** — the submission is complete enough to evaluate, and the requirement
     is not satisfied.
   - **ambiguous** — the submission's content doesn't give you enough to judge.
4. For each finding, include a short `evidence` quote (≤25 words) from the extracted
   content, or for gaps, a brief note on what's missing.

## Boundaries

- Be conservative. When in doubt, return ambiguous, never gap.
- Do not invent gaps that aren't backed by specific evidence.
- Do not comment on quality, style, or judgement issues — only objective requirement
  satisfaction.

## Output shape

Return a JSON array of `{requirement_id, status, evidence}` objects, one per requirement.
```

### 7.3 When to generate `.py` modules instead

Some logic genuinely benefits from being deterministic code rather than model reasoning. For the project workspace, the primary case is **final consolidation of the deliverable**.

Why: when stitching four section Word docs into a master Word doc, applying a template's styles, inserting cross-references, running cross-section numeric reconciliation, you want predictable, repeatable behaviour. Hand this to a code module rather than the model. Format-specific assembly (python-docx for Word, openpyxl for Excel, pdfrw for PDF) is best expressed as code.

At kickoff, the `consolidate` skill triggers a Copilot session to write `$HOME/code/consolidator.py` parameterised against the Charter's deliverable shape. The skill's instructions describe what the module should do; Copilot writes the code; the agent imports and runs it when all tasks are submitted.

**Illustrative consolidator-generation prompt** (sent by the `consolidate` skill at the right moment):

> Write a Python module `consolidator.py` that exports `consolidate(charter: dict, state: dict) -> dict` returning `{"output_path": ..., "reconciliation_findings": [...]}`. For each task in charter.tasks, extract its content from state.submissions[task_id].extracted_content and place it in the corresponding section of the Word template at charter.deliverable.template_path. Apply charter.consolidation_rules to find cross-section findings and return them with specific values and locations. Use python-docx for Word assembly.

The renderer (for the dashboard payload) and the compliance checker may also benefit from generated modules for *some* projects — e.g. if a project needs heavily structured Excel reading. But in most cases, the model can produce the dashboard payload directly via the `status-refresh` skill without a generated module. Treat module generation as the exception, not the default.

### 7.4 Why this design holds up

The "the agent writes code that builds the workspace" beat in any demo is still real — but it's now grounded in a Microsoft-supported pattern (skills + selective module generation) rather than a homegrown "Copilot writes Python on every kickoff" pattern. The skills are visible, auditable, and live in the agent's image. The generated modules — when needed — are bounded to specific high-leverage cases.

It also gives you a much cleaner explanation of *what makes the agent project-aware*: it's the combination of declarative skills (the same across all projects) and the project's Charter (the source of variety). Skills carry the *behaviour*; the Charter carries the *content*. That's a defensible separation.

---

## 8. The autonomous capture loop

This is the operational heart of the system. Every time a user visits the dashboard URL, this runs.

### 8.1 The cycle, on each invocation

The agent, on every `/invocations` call from the frontend:

1. **Loads Charter and state** from `$HOME`.
2. **Identifies the visiting user** from the Foundry-provided OBO token context.
3. **For each task in the Charter**, runs the capture-and-status cycle:
   - Polls each `watch_channel` via WorkIQ in the visiting user's context.
   - Detects new candidate events since `state["last_check"][task_id]`.
   - For each candidate, runs a classification step (agent reasoning, not Copilot): is this a submission / a question / supporting material / unrelated?
   - If submission: extract content via WorkIQ; run `compliance.check_submission()`; update state.
   - If question: surface as an exception for the coordinator.
   - Recompute the task's status using the four-signal triangulation (section 8.3).
4. **Detects new exceptions** (overdue, auto-reply detected, runbook gap, cross-section mismatch).
5. **If the visitor is the coordinator and recent submissions warrant**, drafts suggested actions (nudges, follow-ups) and queues them in the exceptions panel.
6. **If all tasks are submitted**, runs `consolidator.consolidate()` to produce/refresh the final deliverable.
7. **Calls `renderer.render_dashboard()`** with the updated state and the visiting user's identity.
8. **Returns the dashboard payload** to the frontend.

### 8.2 Capture across heterogeneous channels

For each task, the agent watches multiple channels because real people deliver through whatever channel they prefer:

- **SharePoint files**: the assigned templated draft (the canonical path) plus any new file in the project folder whose name matches the task's pattern.
- **Email**: any email from the owner to the coordinator, or to the project alias, with an attachment OR with substantive content in the body.
- **Teams**: any message from the owner in the project channel, plus DMs to the agent itself (if it's wired up as a Teams app).
- **OneDrive**: any file shared with the project by the owner.

The classification step (section 8.4) decides whether a candidate is actually a submission.

### 8.3 Status inference via signal triangulation

For each task, the agent infers status from four independent signals:

| Signal | Source | Used for |
|---|---|---|
| File activity | Graph (last modified on templated draft + any submitted files) | "In progress" vs "stalled" vs "submitted" |
| Teams replies | WorkIQ Teams | Explicit "done" / "complete" signal from owner |
| Email replies | WorkIQ Mail | Same, alternate channel |
| Calendar / OOO | WorkIQ Calendar | Suppresses nudges when owner is on PTO |

Logic:

- No activity AND no signal AND owner available AND <SLA hours since kickoff → **Assigned**, no action.
- No activity AND no signal AND owner available AND >SLA hours since kickoff → **Assigned**, draft nudge suggestion for coordinator.
- File modified within last 48h, no completion signal → **In progress**.
- File modified AND completion signal in Teams/email → **Submitted**.
- No activity AND OOO detected → **Overdue**, draft reassignment suggestion for coordinator. Do not auto-nudge.
- File modified AND content-extraction shows runbook gaps → **Submitted with gaps**, draft clarification suggestion for coordinator.

### 8.4 Classification of captured events

A new event arrives — say, an email from the CFO with an Excel attachment. Is it a submission?

The agent invokes the model with a structured prompt:

- Sender identity (matches task owner?)
- Channel (one of the watch channels?)
- Content signals (file type matches expected? subject/body language suggests submission?)
- WorkIQ-extracted content shape (matches runbook requirements?)
- Surrounding context (was the owner recently asked something? is there a draft thread?)

Returns one of: `submission` / `revised_submission` / `question` / `supporting_material` / `unrelated`. Confidence score attached. Ambiguous cases get a `needs_review` flag, surface as an exception for the coordinator.

This classification logic is part of the generic agent code, not Copilot-generated. It's the same across all projects.

### 8.5 The suggested-action layer

When the agent identifies something the coordinator needs to do — nudge an owner, ask a clarifying question, reassign a task — it doesn't act autonomously. It:

1. Drafts the outbound message (Teams/email content).
2. Adds a suggested action to `state["exceptions"]` with the draft, the target recipient, the reasoning, and an `approve_action_id`.
3. The dashboard's exceptions panel renders these for the coordinator.
4. When the coordinator clicks "approve", the frontend POSTs `{"action": "execute_suggested", "approve_action_id": "..."}`.
5. The agent executes the action via WorkIQ — **in the coordinator's OBO context**, so the Teams message or email is sent as her, not as a bot.

This human-in-the-loop for outbound is the system's core governance property. Do not provide an "auto-approve all" mode.

---

## 9. Build order — recommended sequence

For a coding agent implementing this, here's a sane progression. Each phase produces a runnable artifact.

### Phase 1 — Skeleton (1–2 days)

- Deploy a minimal Foundry hosted agent that accepts Invocations, persists a counter to `$HOME`, returns it. Verify session persistence across cold starts.
- Build a minimal Container App that authenticates via Entra, accepts a project ID in the URL, and calls the agent with the right chat-isolation key.
- Verify two users hitting the same project ID see the same counter; different project IDs see different counters.

### Phase 2 — Charter and kickoff (2–3 days)

- Define the Charter JSON schema and validation.
- Implement the kickoff flow: prompt parsing, WorkIQ-grounded proposal, coordinator ratification, lock + persist.
- Implement the kickoff fan-out: SharePoint folder + templated files, Teams kickoff message, Outlook tasks, emails.
- Verify a real kickoff against a real M365 tenant.

### Phase 3 — Copilot SDK as the agent runtime (2 days)

- Start from the Microsoft sample at `microsoft-foundry/foundry-samples/samples/python/hosted-agents/bring-your-own/invocations/github-copilot`. Treat it as your reference implementation.
- Wire `CopilotClient` as the agent's runtime via the dedicated `copilot_runtime.py` module (sole owner of the client); reuse the Copilot session across invocations within the same Foundry session.
- Use the **GitHub PAT route**: a fine-grained `GITHUB_TOKEN` with *Copilot Requests → Read-only*, injected from Key Vault by `agent.yaml`. Do **not** set `AZURE_AI_MODEL_DEPLOYMENT_NAME` — the Copilot SDK would silently flip to a Foundry-model backend ([AGENTS.md §4.2](../AGENTS.md#42-model-assignment-policy)). Boot-time assertion enforces this.
- Implement the initial set of skills (`project-kickoff`, `status-refresh`, `capture-classify`) under `skills/`.
- For the consolidator specifically (where deterministic code is warranted), implement the kickoff-time generation of `$HOME/code/consolidator.py`.
- Test against the canonical board pack scenario. Build with `azd deploy` from the start — do not local-build images.

### Phase 4 — Capture loop (3–4 days)

- Implement watch-channel polling via WorkIQ for each channel type.
- Implement the classification step.
- Implement the status-triangulation logic.
- Implement state-file updates.

### Phase 5 — Dashboard (2–3 days)

- Build the frontend renderer for the dashboard payload.
- Implement exceptions panel with approval flow.
- Implement coordinator chat input for free-text instructions.

### Phase 6 — Consolidation and closure (2 days)

- Implement the consolidate-on-all-submitted trigger.
- Implement Charter amendment flow with Copilot regeneration.
- Implement the project-close flow (export final deliverable, archive state, mark session for dissolution).

### Phase 7 — Hardening (ongoing)

- Audit logging.
- Error handling on every WorkIQ call (transient failures, permission denials).
- Idempotency on action execution (don't double-send a Teams message if the approval click is replayed).
- Conditional Access edge cases.

---

## 10. Things the coding agent should keep in mind — easy to miss

These are the gotchas, design subtleties, and constraints that don't show up in feature lists but will break the system if forgotten.

### 10.1 Identity is the single most important thing to get right

Every WorkIQ call must run in the *visiting user's* OBO context. Never use the agent's own managed identity for content access. The reason: Microsoft 365's permission model, sensitivity labels, and compliance policies are enforced at the user level, and using the agent's identity would either (a) fail because the agent has no M365 license, or (b) bypass user-level governance, which is a serious security and compliance issue. WorkIQ's docs are explicit that application-only auth is not supported.

When the coordinator approves an outbound action, that action runs in *her* context. The Teams message is sent as her. The email is sent as her. Do not send anything as the agent identity to humans — that breaks the trust model and looks like spam.

### 10.2 The dashboard's data freshness is asymmetric

A task owner's visit only refreshes data their user can see. The coordinator's visit refreshes everything. Display the "last fully refreshed at" timestamp prominently so observers know their view's freshness. Do not try to "fix" this by using a service identity — it's a feature, not a bug.

### 10.3 Idempotency on every outbound action

The frontend may retry an approve-action POST on network errors. The agent may be invoked twice with the same approval ID. Make sure executing a suggested action twice doesn't send two Teams messages. The pattern: each suggested action has a UUID; the state file records `executed_action_ids`; the agent checks before executing.

### 10.4 The Charter is immutable except via the amendment flow

Don't let any path in the code write to `charter.json` except the kickoff flow and the amendment flow. Both must run through ratification. The Charter being trustable is the foundation for everything else; quiet drift would be a catastrophic bug.

### 10.5 Watch the 15-minute idle timeout

Cold-starting from idle is fast but not instant. The frontend should show a "warming up" state for the first 2–5 seconds of any invocation that follows a long idle. Don't show a stale screen during cold-start.

### 10.6 30-day session lifetime is a hard ceiling

If a project legitimately runs longer than 30 days, plan for it: at the 25-day mark, the agent should proactively offer to "renew" — which means kicking off a new session keyed by a new chat-isolation key, migrating state, and redirecting the URL. Don't let projects silently die at day 30.

### 10.7 Use the Invocations protocol, not Responses

Responses is a higher-level protocol with platform-managed conversation history. For this system, we manage state ourselves in `$HOME`. Invocations gives full control and avoids paying the cost of conversation history we don't use.

### 10.8 Do not expose ports from the sandbox

The sandbox is not a web server publishable to the public internet. The Container App is the public face. All dashboard rendering goes through the agent's Invocations endpoint. Don't try to expose Streamlit or any other server from inside.

### 10.9 The `watch_channels` schema needs to be extensible

Different project kinds will need different channel types over time (e.g. a Jira watch channel for engineering projects, a Slack watch channel if you ever integrate). Design the channel-watching code as a registry of handlers keyed on `channel.kind`, with each handler implementing a common `poll(charter, task, since) -> list[CandidateEvent]` interface. Don't hardcode the channel types.

### 10.10 Copilot SDK session lifecycle

The Copilot SDK runs in-process — `from copilot import CopilotClient` — no CLI invocation by your code. `CopilotClient.start()` handles whatever underlying machinery the SDK uses.

The Copilot SDK session SHOULD be long-lived and reused across Foundry agent invocations within the same Foundry session — cache it in memory keyed by `FOUNDRY_AGENT_SESSION_ID` and resume rather than recreate. This is exactly how the official Microsoft sample does it. Each new Foundry session creates a new Copilot session; subsequent invocations within that Foundry session reuse the cached Copilot session.

The only time you create or destroy Copilot sessions is at Foundry session boundaries — first invocation creates, end-of-life teardown destroys. Do NOT create a new Copilot session per invocation; that throws away the conversational state and re-pays cold-start costs.

### 10.10a Build for linux/amd64 — Apple Silicon will catch you

Foundry hosted agents require `linux/amd64` images. If you build locally on an Apple Silicon Mac (or any ARM64 host), the default `docker build` produces an ARM64 image that fails at runtime in Foundry. The error message is not always obvious — it can look like the agent starts but crashes on first request.

Two ways to avoid this:

- **Preferred:** Deploy with `azd deploy`, which uses ACR remote build and always produces the right architecture. This is what the official samples recommend.
- **If building locally:** Force the platform with `docker build --platform=linux/amd64 -t image .`. This works on ARM64 hosts via Docker's emulation.

Mention this in your build runbook. It's a 30-second fix once you know about it and an hour of confusion if you don't.

### 10.11 Permissions on the templated files

When the agent creates a templated draft file for an owner, it should grant the owner edit access AND keep the coordinator with edit access. When a task is reassigned, revoke the prior owner's access (don't just add the new owner). Use Graph API for permission changes.

### 10.12 Real classification will sometimes be wrong

The "is this email a submission?" classification will occasionally misfire. Always show the user *what was captured* and *from where* on the dashboard tile, and give the coordinator a "this isn't actually the submission" override button that re-marks the task as pending and ignores that source.

### 10.13 Audit log everything

Every state mutation, every outbound action, every Charter amendment, every Copilot-generated file — log to `$HOME/activity.json` AND emit an OpenTelemetry span. The audit log is the project's auditable record. In regulated industries, this is the difference between adoptable and not.

### 10.14 The project_id needs to be URL-safe and human-readable

`board-nov-2025` not `a8c7f23e9b1d4f5a`. The coordinator may share this URL in many places; an opaque ID looks suspicious. Generate slug-style IDs from the project goal at kickoff and ensure uniqueness within the tenant.

### 10.15 Handle the "coordinator forgot to ratify" case

If the coordinator types a kickoff prompt then closes their browser before ratifying, the session sits with a proposed-but-unratified charter. On her next visit, the dashboard should show the pending ratification, not stale state.

### 10.16 Licensing and tenant setup prerequisites

These are real operational dependencies but most are likely already in place if your tenant has been using WorkIQ at all:

- **Microsoft 365 Copilot license** for every user (coordinator, owners, observers). Without it, WorkIQ calls fail for that user.
- **Agent 365 tenant enablement** (`Enable-WorkIQToolsForTenant.ps1`) must have been run by a tenant admin once. **Verification**: if any Foundry-portal-created agent in your tenant can already call WorkIQ tools, this is done.
- **Foundry project WorkIQ MCP connections** with OAuth Identity Passthrough enabled. Configure once per Foundry project via the portal.
- **A frontend app registration with basic sign-in scopes (`User.Read`)** — for the Container App. This is the only registration the implementing team needs to arrange, and it does NOT require admin consent for WorkIQ-specific scopes. Reuse an existing internal SSO app registration if your tenant has one; otherwise self-register if your tenant allows.

Do NOT plan for: a custom app registration with `WorkIQAgent.Ask` or per-WorkIQ-server delegated permissions. That pattern is for non-Foundry third-party agents calling WorkIQ directly. Foundry-hosted agents using portal-configured MCP connections skip this entirely — the platform pre-grants those scopes for you.

If app registration is genuinely locked down in the tenant, the cleanest fallback is to publish the agent as a Teams app. Foundry's docs are explicit that when agents are invoked through Microsoft 365 channels (especially Teams), the OBO flow is wired up automatically. No custom frontend, no new app registration. Trade-off: the dashboard becomes adaptive cards inside Teams instead of a custom web URL, which is a meaningfully different UX but eliminates the registration dependency entirely.

### 10.17 Be explicit about what the agent will NOT do

In the kickoff confirmation step, surface the agent's boundaries clearly: it will not autonomously contact owners; it will draft and surface for approval; the coordinator can reassign, amend, or close at any time. This sets the right expectations from minute one.

### 10.18 Don't store M365 content in `$HOME` long-term

Extracted content from WorkIQ should be summarised into the state file (highlights, compliance results) but the raw extracted content should be ephemeral — re-fetched on demand. Storing copies of email bodies, file contents, etc. in the sandbox creates a data-residency and retention problem you don't need.

---

## 11. The functional spec, summarised

> A coordinator types a project kickoff prompt into a shared web URL. A Foundry-hosted agent grounds the prompt in similar prior projects via WorkIQ, proposes a structured Project Charter, gets it ratified, and executes the kickoff — creating SharePoint structure, sending tagged Teams messages, individual emails, and Outlook tasks to each assigned owner. The agent then uses GHCP Copilot SDK inside its session sandbox to write project-specific modules: a dashboard renderer, a runbook-compliance checker, and a final consolidator — each shaped by the Charter.
>
> Owners deliver their content via whatever channel they prefer. Every visit to the dashboard URL wakes the agent, which polls each task's watch channels in the visiting user's OBO context, classifies new events, extracts content, runs compliance checks, infers status, and re-renders. When the coordinator visits, the agent additionally surfaces drafted suggested actions — nudges, follow-ups, reassignment proposals — for her approval. Approved actions execute in her OBO context.
>
> When all tasks are submitted, the agent runs the consolidator to produce the final deliverable. The coordinator reviews, the deliverable is exported to SharePoint as the durable record, the project is closed, and the session dissolves.
>
> The same agent, the same Container App, the same code handles every project across the firm. The variety is captured entirely in per-project Charters and per-project Copilot-generated modules — both living in per-project session sandboxes, both dissolving cleanly when the project ends.

---

## 12. Appendix: minimum viable demo scope

If building this for a 10-minute demo first, scope it to:

- One project kind (board pack).
- One coordinator persona, four task owner personas, mock-able M365 data.
- Kickoff → 4 templated files + Teams kickoff message + emails (real or mocked).
- Capture from SharePoint file activity + Teams replies (the other channels stubbed).
- Triangulation status, dashboard rendering, exceptions panel.
- One Copilot-generated module visible to the audience (the dashboard renderer is the most demo-visible).
- One reconciliation finding fires at consolidation.
- Project close → session dissolve.

Skip for the demo: OneDrive watch channel, auto-reply detection beyond a single hardcoded case, Charter amendment, multi-project concurrency (single project is fine for a demo).

Plan ~5–7 build days from a coding agent for the demo scope, ~3–4 weeks for production hardening.