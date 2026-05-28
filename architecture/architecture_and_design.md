# Architecture & Design — charter-agent

> The implementation-level companion to [`../functional-specs/project_workspace_spec.md`](../functional-specs/project_workspace_spec.md). The requirement spec answers *what* and *why*. This document answers *how* — concrete components, contracts, and the seams where the host runtime and the Toolbox plug into otherwise generic agent code.
>
> Read [`../AGENTS.md`](../AGENTS.md) first for the non-negotiable invariants this design has been shaped around. The contract there is authoritative; this document elaborates it.

**Status**: v0.5 (May 26, 2026). Two skills ship (`sow-response` and `general`); multi-project sidebar; per-project Foundry session model; client-side view cache and transcript persistence. For narrative explanations of the agent backend and the desktop client, see [`../agent/README.md`](../agent/README.md) and [`../desktop-client/README.md`](../desktop-client/README.md) respectively — this document covers the implementation contracts.

**Navigation:** [Root README](../README.md) · [Agent README](../agent/README.md) · [Desktop client README](../desktop-client/README.md) · [AGENTS.md](../AGENTS.md)

---

## 1. Design principles, restated as decisions

| Principle (from spec) | Concrete design decision |
|---|---|
| **Skills-first, agentskills.io-conformant** | Every reusable capability is packaged as an Agent Skill under `agent/skills/{name}/` with a valid `SKILL.md`. A small in-repo loader (`runtime/skill_loader.py`) reads each `SKILL.md`, validates the frontmatter, and constructs one warm MAF `Agent` per skill. See [AGENTS.md §4.3](../AGENTS.md#43-agent-skills-format-agentskillsio-conformance) for the format contract and [§4.4](../AGENTS.md#44-core-code-vs-skill--the-decision-rule) for the core-vs-skill decision rule. Today two skills ship at the top level: `general` (default router) and `sow-response` (SOW workflow orchestrator). `sow-response` further delegates to five sub-skills (`charter-draft`, `kickoff-extract`, `reply-poll`, `rfp-search`, `task-allocate`) via the `invoke_skill` tool. |
| Generic over project-specific | Two layers of variability: **Charter (data, today a markdown document at `$HOME/project_charter.md`)** → **Agent Skills (declarative behaviour)** → generic agent (constant). Nothing else varies per project. |
| **Identity passthrough for WorkIQ** | The desktop client authenticates the *end user* and attaches their bearer to `/responses`. The Foundry runtime exchanges that identity into a WorkIQ token internally per Toolbox connection. The agent process holds no WorkIQ refresh tokens and runs no OBO flow. See [§7](#7-identity--auth) for the full flow and [`../spike/desktop_to_foundry/`](../spike/desktop_to_foundry/) for the spike that established this. |
| No server-side background workers | The agent on Foundry only runs when `/responses` is called — there is no autonomous wake-up loop in the agent process. The desktop client, however, includes an opt-in background poller that wakes on a configurable interval (`CHARTER_POLL_INTERVAL_MINS`, default 30) and triggers a capture/status-refresh turn for projects whose active skill declares `metadata.background_sync: true`. Polling is human-identity-driven (uses the signed-in user's bearer) and confined to the desktop client process. |
| State lives in `$HOME` | `state.py` is the only module that touches files in `$HOME`. Atomic write-via-temp-then-rename for every mutation. The MAF `AgentSession` thread persists to `$HOME/agent_session/` and is resumed across calls. |
| Human-in-the-loop outbound | The skill drafts outbound; the user approves; the skill sends in the user's identity (which has already been propagated by Foundry). |
| Single runtime | One MAF `Agent` (from `agent_framework`, **not** `ChatAgent`) on a Foundry `gpt-5.x` deployment via Managed Identity. No second LLM path, no codegen sub-agent. |

---

## 2. Component diagram

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  Tenant boundary                                                               │
│                                                                                │
│  Desktop client (pywebview)                                                    │
│    • MSAL / WAM sign-in → bearer token (scope: https://ai.azure.com/.default)  │
│    • Multi-project sidebar: each project stores its own agent_session_id       │
│    • Sends [charter-agent-context: project_id=p-xxx skill=sow-response]        │
│      preamble on every prompt                                                  │
│    • Handles oauth_consent_request SSE (opens browser, retries w/ prev_id)     │
│    • Caches dashboard + activity in view_cache.json for hosted mode            │
│                                                                                │
│              │  POST /responses                                                │
│              │  Authorization: Bearer <user_token>                             │
│              │  Body: {input, stream:true, agent_session_id, prev_response_id} │
│              ▼                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  Foundry hosted agent                                                    │  │
│  │                                                                          │  │
│  │  _ResilientResponsesHostServer                                           │  │
│  │    • Parses [charter-agent-context:] preamble → sets active project      │  │
│  │    • Routes to warm Agent for resolved skill                             │  │
│  │                                                                          │  │
│  │  MAF Agent per skill (warm, built at boot)                               │  │
│  │    ├─ FoundryChatClient → gpt-5.x (Managed Identity)                     │  │
│  │    ├─ instructions: SKILL.md body                                        │  │
│  │    └─ tools:                                                             │  │
│  │         • MCPStreamableHTTPTool → Charter-Agent-Tools Toolbox            │  │
│  │         • skill in-process tools (dashboard_payload, record_submission…) │  │
│  │         • state_tools (read/write/append $HOME)                          │  │
│  │                                                                          │  │
│  │  Foundry runtime (platform)                                              │  │
│  │    • Routes agent_session_id → persistent microVM                        │  │
│  │    • Validates user bearer                                               │  │
│  │    • On Toolbox MCP call: substitutes user identity (Identity            │  │
│  │      Passthrough); emits oauth_consent_request on first access           │  │
│  │    • Auto-instruments: App Insights spans, OTel traces                   │  │
│  │                                                                          │  │
│  │  Per-project microVM $HOME                                               │  │
│  │    .active_project              (current project_id pointer)             │  │
│  │    projects/<pid>/                                                       │  │
│  │      project_charter.md        (ratified at kickoff, immutable)          │  │
│  │      project_log.json          (tasks, submissions, cursors, status)     │  │
│  │      activity.json             (append-only NDJSON audit trail)          │  │
│  │    agent_session/<id>.json     (MAF thread persistence)                  │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│              │  MCP (Streamable HTTP)                                          │
│              │  Authorization: Bearer <agent_MI_token>                         │
│              │  Foundry-Features: Toolboxes=V1Preview                          │
│              ▼                                                                 │
│  Charter-Agent-Tools Toolbox (Foundry-managed, preview)                        │
│    Single MCP endpoint → 8 WorkIQ M365 Intelligence servers (135 tools)        │
│    Mail · Calendar · Teams · Files · Word · OneDrive · User · Copilot          │
│    User identity: from Foundry passthrough (not agent credentials)             │
│                                                                                │
│  App Insights / OpenTelemetry                                                  │
│    Auto-wired by ResponsesHostServer. ProcessAttributesSpanProcessor stamps    │
│    project.id on every span. activity.json is the product-level audit trail.   │
└────────────────────────────────────────────────────────────────────────────────┘
```

The only public surface is `/responses` on the hosted agent. Everything else is private to the tenant.

---

## 3. Repository layout

The repo structure is in [`../AGENTS.md` §5](../AGENTS.md#5-repository-layout-target). The modules that exist today, with their responsibility:

| Module | Responsibility |
|---|---|
| `agent/src/charter_agent/__main__.py` | Boot entry: assert env, enable tracing, warm the runtime, start `ResponsesHostServer`. |
| `runtime/foundry_host.py` | Sole owner of the MAF `Agent` and `FoundryChatClient`. Constructs the warm `Agent` with the `sow-response` skill body as `instructions`, the Toolbox `MCPStreamableHTTPTool` and `state_tools` as `tools`. Owns the auth-injecting `httpx.AsyncClient` event hook that stamps `Foundry-Features: Toolboxes=V1Preview` and the `Authorization` bearer on every Toolbox request (Toolbox channel auth — see [§7](#7-identity--auth)). |
| `runtime/skill_loader.py` | Reads `agent/skills/*/SKILL.md`, validates the agentskills.io frontmatter, returns the body string for injection as `Agent.instructions`. |
| `runtime/state_tools.py` | MAF `@tool`-decorated wrappers around `state.py`: `read_text`, `write_text`, `read_json`, `write_json`, `append_ndjson`, `file_exists`, `list_files`. The skill drives all `$HOME` I/O through these. |
| `orchestrator.py` | Three-verb dispatcher (`echo`, `list_tools`, `run_skill`). Vestigial from the Phase-1 Invocations-protocol surface; the production surface today is the Responses protocol served directly by `__main__`. |
| `state.py` | Atomic read/write of every `$HOME` file. Path-containment-checked. Returns plain values; the skill owns the JSON shape. |
| `workiq/` | Placeholder package. Skills call WorkIQ through the Toolbox `MCPStreamableHTTPTool` directly today. |
| `observability.py` | Re-exports `@trace_function`, owns `log_activity(...)` which appends to `$HOME/activity.json`. Process-wide span attributes are stamped by `ProcessAttributesSpanProcessor.on_start`, registered once at boot. |

Aspirational modules from the spec (`charter/`, `kickoff/`, `capture/handlers/*`, `status/`, `actions/`, `consolidation/`) **do not exist**. They are promoted out of the skill only when the [AGENTS.md §4.4](../AGENTS.md#44-core-code-vs-skill--the-decision-rule) decision rule says they have earned a Python home.

---

## 4. The shipped skills

Two top-level skills ship today:

- **`general`** — pure fallback chat skill. Only invoked when the **server-side first-turn classifier** (in [`runtime/responses_host.py`](../agent/src/charter_agent/runtime/responses_host.py)) cannot confidently match a workflow skill on the first message of a new project, or when the persisted skill on `project_log.json` is `general`. Holds no routing logic of its own — it replies helpfully and suggests the user open a new project for any clearly-workflow request. `metadata.background_sync: false` (no autonomous polling for `general`-only projects).

  On the first message of a new project, `_ResilientResponsesHostServer._handle_inner_agent` makes one short non-tool LLM call through the shared warm `FoundryChatClient`, scoring the user's text (capped at 2000 chars) against every registered skill's `description` frontmatter, and dispatches directly to the winning skill (defaulting to `general` on error/ambiguity). The choice is persisted to `project_log.json`, so all subsequent turns dispatch without re-classifying — one classifier call per project lifetime. There is no client-side auto-trigger or hidden second request; the user always sees one bubble per turn.
- **`sow-response`** — the SOW workflow orchestrator. End-to-end SOW response workflow — first-run mode (kickoff) and resume mode (capture + status update + consolidation). Generic across customers/projects; per-engagement variety lives in the Charter it reasons against. Operates as a phase-driven orchestrator: the body calls `invoke_skill(...)` to delegate each workflow stage to a dedicated sub-skill (`charter-draft`, `kickoff-extract`, `reply-poll`, `rfp-search`, `task-allocate`) under `agent/skills/sow-response/`. The orchestrator owns sequencing, halt-on-failure, and the dashboard payload (`publish_view`); the sub-skills own the per-phase reasoning and tool dispatch.

Structure of `sow-response`:

- [`agent/skills/sow-response/SKILL.md`](../agent/skills/sow-response/SKILL.md) — the orchestrator body. Owns phase sequencing, mode-detect (first-run vs resume based on `project_log.json`), halt-on-failure, dashboard payload assembly. Delegates per-phase reasoning to sub-skills via `invoke_skill(...)`.
- Sub-skills (each is a folder with its own `SKILL.md` under `agent/skills/sow-response/`):
  - [`rfp-search/`](../agent/skills/sow-response/rfp-search/SKILL.md) — locate the RFP in the user's M365 environment via WorkIQ Mail/Files.
  - [`charter-draft/`](../agent/skills/sow-response/charter-draft/SKILL.md) — draft `project_charter.md` from the grounded RFP + kickoff context.
  - [`kickoff-extract/`](../agent/skills/sow-response/kickoff-extract/SKILL.md) — extract owners, tasks, and runbook items from the kickoff meeting transcript.
  - [`task-allocate/`](../agent/skills/sow-response/task-allocate/SKILL.md) — fan out kickoff briefs (Teams DMs for internals, emails for externals) and seed `project_log.json`.
  - [`reply-poll/`](../agent/skills/sow-response/reply-poll/SKILL.md) — poll Mail/Teams for new activity since the last cursor, classify per [`references/CLASSIFICATION_RUBRIC.md`](../agent/skills/sow-response/references/CLASSIFICATION_RUBRIC.md), update task status, draft (but never auto-send) follow-ups.
- `references/` (shared by orchestrator + sub-skills):
  - [`SOW_SECTIONS.md`](../agent/skills/sow-response/references/SOW_SECTIONS.md) — section taxonomy
  - [`COMMUNICATION_MATRIX.md`](../agent/skills/sow-response/references/COMMUNICATION_MATRIX.md) — channel choice per task type
  - [`CLASSIFICATION_RUBRIC.md`](../agent/skills/sow-response/references/CLASSIFICATION_RUBRIC.md) — submission vs question vs supporting vs unrelated

The skill body is loaded once at boot as the warm `Agent`'s `instructions`. It is the source of truth for the state-file shape (see [§5](#5-state)). A change to the JSON shape is a change to the skill body, reviewed as code.

---

## 5. State

State files live in the per-session Foundry microVM `$HOME`. Shape is owned by the skill body; there is no Pydantic schema today.

| File | Format | Shape (current) |
|---|---|---|
| `project_charter.md` | Markdown | The ratified charter document the agent emits at kickoff and treats as immutable thereafter. Schema is whatever the skill's §3–§4 prompt produces. |
| `project_log.json` | JSON | Per-project working state. Top-level keys include `project_id`, `customer`, `status` (enum: `kicked_off` / `submitted` / `submitted_with_gaps` / `closed`), `kickoff_sent`, and `tasks[]` with per-task `task_id`, `title`, `owner_upn`, `status`, `kickoff_sent`, `last_polled_at`, `submissions[]`, `runbook_requirements`. The skill body is authoritative — read [§9 of `sow-response/SKILL.md`](../agent/skills/sow-response/SKILL.md) before changing the shape. |
| `activity.json` | NDJSON (append-only) | One object per line: `{at, actor, kind, summary}`. Used both as the human-facing audit narrative and as a recovery trail. Written by `observability.log_activity(...)`. |
| `agent_session/<session-id>.json` | JSON | MAF `AgentSession` thread persistence (owned by MAF, not by the skill). |
| `state.json` | JSON | Legacy Phase-1 counter for the `echo` verb. Don't add new fields here. |

A file is promoted to a Pydantic model under a new `charter/schemas.py` only when (a) a second Python module reads or writes it, or (b) an external typed surface needs to consume it.

---

## 6. Foundry Toolbox via native MAF `MCPStreamableHTTPTool`

The Toolbox is consumed by the host `Agent` through MAF's first-class `MCPStreamableHTTPTool` (from `agent_framework._mcp`). There is no hand-rolled bridge in production. [`samplecode_toolbox.py`](samplecode_toolbox.py) (portal-generated) is kept as a wire-shape debugging aid only — its hand-rolled `McpBridge` is exactly what `MCPStreamableHTTPTool` replaces.

Wiring requirements, every row a hard requirement of the Toolbox MCP endpoint as it stands today:

| Concern | Detail | Where |
|---|---|---|
| Endpoint URL | `{FOUNDRY_PROJECT_ENDPOINT}/toolboxes/{TOOLBOX_NAME}/versions/{TOOLBOX_VERSION}/mcp?api-version=v1` in dev (version pinned); `{FOUNDRY_PROJECT_ENDPOINT}/toolboxes/{TOOLBOX_NAME}/mcp?api-version=v1` (consumer endpoint) in prod. | `runtime/foundry_host.py` |
| Toolbox channel auth | `DefaultAzureCredential` against `https://ai.azure.com/.default` (Foundry-assigned Managed Identity in prod; the developer's `az login` identity locally). | `runtime/foundry_host.py` |
| Mandatory header | `Foundry-Features: Toolboxes=V1Preview` on every request. | `httpx.AsyncClient` `event_hooks={"request": [...]}` in `runtime/foundry_host.py` |
| `Authorization` header | Bearer for the Toolbox-channel call (Managed Identity / `az login` token). MAF's `header_provider` only fires on `tools/call` — **not** on `connect()` / `initialize` / `tools/list` — so auth must live in the `event_hooks` request hook, not `header_provider` alone. | same |
| User identity propagation to WorkIQ | **Not done by us.** The user's bearer is attached to `/responses` by the client; the Foundry runtime substitutes the user identity into the MCP call when invoking a Toolbox connection marked "Identity Passthrough". See [§7](#7-identity--auth). | platform |
| `load_prompts=False` | The Foundry Toolbox does not implement MCP `prompts/list`; with default `load_prompts=True`, `connect()` raises HTTP 400. | `runtime/foundry_host.py` |
| `approval_mode="never_require"` | For reads and writes. Approval is enforced at a higher layer (the skill body refuses to call write tools without explicit user OK in the prompt). | `runtime/foundry_host.py` |
| Capture-loop concurrency | Fan out channel polls concurrently (`asyncio.gather`) — the 100-second non-streaming MCP-call timeout is per-call, not per-batch. | the skill body (today) |
| Transitive deps | Declare in `pyproject.toml`: `mcp` (used by `streamable_http_client`) and `aiohttp` (used by `azure-ai-projects` async pipeline). | `agent/pyproject.toml` |

**Standing rule when extending beyond what's documented.** For anything not directly handled by `MCPStreamableHTTPTool` — a new Toolbox tool, a different MCP method, a changed input schema, a bumped MCP `protocolVersion` — introspect the **live Toolbox endpoint** (`tools/list`, `initialize` capabilities) rather than coding against a stale snapshot. Reference the Microsoft Learn docs cited in [`../functional-specs/references.md` §8](../functional-specs/references.md). If the portal regenerates `samplecode_toolbox.py`, replace it and update this section in the same PR.

The Toolbox bundles 8 WorkIQ MCP servers (135 tools as of May 2026): Mail, Calendar, Files (SharePoint/OneDrive), Teams, Word, OneDrive, User, Copilot. The exact tool list and schemas are discovered at runtime; do not hard-code them.

---

## 7. Identity & auth

The single most important architectural decision in the codebase, and the one most at odds with earlier drafts of this document. The pattern is **OAuth Identity Passthrough at the Foundry MCP connection layer**.

### 7.1 The flow

```
1. Client (desktop) signs the end user in.
     MSAL public client OR az login OR Windows broker.
     Token scope: https://ai.azure.com/.default.

2. Client POSTs to <agent>/responses with
       Authorization: Bearer <user_token>
       Content-Type: application/json
       Accept: text/event-stream
       Body: {"input": "...", "stream": true, "store": false, "previous_response_id": "..."}

3. Foundry runtime validates the user identity and routes to the warm Agent.
   Agent reasons; model decides to invoke a Toolbox tool (e.g. workiq_calendar_list).

4. On the first call per (user, Toolbox connection):
   - Foundry pauses the response stream.
   - It emits an SSE event with substring "oauth_consent_request" and a Microsoft
     login URL in the payload (consent_url / authorization_url).
   - Client opens the URL in a browser; user consents to the specific WorkIQ
     connection once.
   - Client re-POSTs to /responses with previous_response_id set to the paused
     response's id, body input may be empty or a short nudge ("continue").

5. Foundry resumes the same turn; MCP call now succeeds in the user's context;
   tool result contains the user's actual M365 data.
```

The agent process never holds a WorkIQ refresh token, never signs a JWT, and never runs OBO. The Toolbox channel itself is authenticated separately by the agent's Managed Identity (see [§6](#6-foundry-toolbox-via-native-maf-mcpstreamablehttptool)) — that token is for *reaching* the Toolbox endpoint, not for *acting as* the user inside the MCP call.

### 7.2 What was tried and shelved

The original plan was a confidential-client app registration owned by us, with admin-consented delegated permissions on the WorkIQ resource APIs and server-side OBO exchange inside the agent (the `runtime/workiq_token.py` / `SOW_OWNER_OBO_*` machinery). That dead-ended in the microsoft.com test tenant for two independent reasons:

1. **No admin consent available.** `sansri@microsoft.com` cannot grant admin consent in the microsoft.com tenant (`Forbidden / RequestDenied` from Graph). Any architecture that requires admin-consenting a fresh app reg is blocked.
2. **No `api://` SPN on WorkIQ.** Both WorkIQ resource apps present in the tenant (`89539be0…`, `15a786a1…`) expose **zero** `oauth2PermissionScopes` and have no `api://` Service Principal — they are *client* apps, not resource APIs. Even with admin rights there is nothing for a custom app to request delegated permissions against.

The dead modules and env vars (`runtime/workiq_token.py`, `runtime/workiq_token_cache.py`, `scripts/bootstrap_workiq_token.py`, `scripts/setup_obo_app_reg.ps1`, `SOW_OWNER_OBO_TENANT_ID/CLIENT_ID/CLIENT_SECRET`, `WORKIQ_SCOPE`) were removed in the May 22 cleanup.

### 7.3 Client surface

The client surface is the **desktop client** under [`../desktop-client/`](../desktop-client/). It signs the user in via Windows Account Manager (WAM) or the system browser, caches the `AuthenticationRecord` for silent re-auth on subsequent launches, and POSTs the user bearer to `/responses`. See [`../desktop-client/README.md §3`](../desktop-client/README.md#3-authentication) for the full auth flow.

The earlier spike under [`../spike/desktop_to_foundry/`](../spike/desktop_to_foundry/) is retained as a minimal proof of the identity-passthrough pattern. The production client is `desktop-client/`.

---

## 8. The `/responses` surface

The agent has no action-verb dispatcher. It serves the OpenAI-compatible Responses protocol on `/responses` and streams the standard SSE event sequence (`response.created` → `response.output_text.delta` → `response.completed`, plus tool-call events and `oauth_consent_request` when applicable). Multi-turn continuity is the caller's responsibility: pass `previous_response_id` on every request after the first.

The warm `Agent` is constructed once at boot in `runtime/foundry_host.bootstrap()` with:

- `client = FoundryChatClient(project_endpoint=..., model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"], credential=DefaultAzureCredential())`
- `instructions = <sow-response/SKILL.md body>`
- `tools = [<MCPStreamableHTTPTool toolbox>, <state_tools...>]`
- `default_options = {"store": False}` — history is owned by `ResponsesHostServer` via `previous_response_id`, not by the upstream model.

The class is `agent_framework.Agent` — there is no `ChatAgent` at the `agent_framework` top level in 1.4.x / 1.6.x.

If a future need genuinely demands a typed envelope (e.g. a client wants `dashboard` payloads with strict shape), express it as a new MAF tool on the warm `Agent`, not as a parallel protocol surface.

---

## 9. Observability & audit

Two layers, decoupled by ownership:

1. **App Insights / OTel spans** — owned by the platform. Server-side root spans, model calls, and tool calls are auto-emitted by `ResponsesHostServer` once App Insights is connected. Client-side spans come from `AIProjectInstrumentor().instrument()` (gated by `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true`) plus any function decorated with `@trace_function` (re-exported from `observability`). Process-wide attributes (`project.id`, `gen_ai.conversation.id`) are stamped by `ProcessAttributesSpanProcessor.on_start`, registered once at boot. No hand-rolled span context managers, no manual exporter wiring.
2. **`$HOME/activity.json`** — owned by us, written via `observability.log_activity(...)`. This is the project's narrative audit trail, exportable to SharePoint at close. It is product behaviour, not telemetry, and is decoupled from the OTel pipeline.

---

## 10. Security model

| Concern | Mitigation |
|---|---|
| WorkIQ called as the wrong identity | Foundry's Identity Passthrough substitutes the calling user into the MCP call. The agent's Managed Identity reaches the Toolbox endpoint but does not impersonate inside it. |
| Outbound spam from the agent | The skill body never sends without an explicit user OK in the prompt; outbound goes out in the user's identity (via passthrough), creating a paper trail. |
| Double-execution on retry | The skill dedupes submissions by `internetMessageId` in `project_log.json`. When/if an `actions/` module lands, the dedupe set is promoted to `state.executed_action_ids`. |
| Charter tampering | Only the kickoff and (future) amendment paths in `sow-response/SKILL.md` write `project_charter.md`. The skill treats it as immutable thereafter. |
| Cross-project data leakage | The desktop client sets `x-agent-chat-isolation-key = project_id` and `?agent_session_id=project_id` on every call. Foundry guarantees per-session microVM `$HOME` isolation. |
| Long-lived secrets in the sandbox | None. The Managed Identity supplies the Toolbox-channel token; the user bearer is per-request. The removed OBO confidential-client secret is no longer needed. |
| Stored M365 content residency | Only summaries persist in `project_log.json`. Raw extracted content is re-fetched on demand. |
| Conditional Access surprises | Surfaced as a normal auth failure to the client; the consent-request flow naturally re-prompts. |

---

## 11. Dependency manifest

Pinned in `agent/pyproject.toml`. Mirrors [`../AGENTS.md` §11.9](../AGENTS.md#119-dependency-manifest).

Runtime: `agent-framework-core`, `agent-framework-foundry`, `agent-framework-foundry-hosting` (transitively pulls `azure-ai-agentserver-responses`), `mcp`, `aiohttp`, `azure-ai-projects>=2.0.0`, `azure-identity`, `httpx`, `python-dotenv`, `pydantic` v2, `pyyaml`, `opentelemetry-api`, `opentelemetry-sdk`, `azure-core-tracing-opentelemetry`, `azure-monitor-opentelemetry`.

Dev/CI: `pytest`, `pytest-asyncio`, `respx`, `freezegun`. `import-linter`, `ruff`, `pyright` are aspirational — not yet wired in CI.

Desktop client ([`../desktop-client/`](../desktop-client/)): `pywebview`, `httpx`, `azure-identity`, `azure-identity-broker` (optional, enables WAM). See `desktop-client/requirements.txt`.

---

## 12. Test strategy

Mirrors [`../AGENTS.md` §11.8](../AGENTS.md#118-test-matrix). 23 tests pass today across `state.py`, `runtime/state_tools.py`, `runtime/skill_loader.py`, `workiq/__init__.py`, `orchestrator.handle_invocation`, and the early Pydantic helpers under `charter/schemas.py`. Every test that touches WorkIQ or external services must mock the boundary (`respx` for HTTP).

Aspirational layers (Foundry-host warm-Agent reuse, capture-handler cursor correctness, classifier golden files, `SuggestedAction` idempotency, end-to-end against a real tenant) are gated on the corresponding modules landing.

---

## 13. Open items

- Decide whether to promote any of `charter/`, `capture/`, `actions/`, `consolidation/` into Python — apply the [AGENTS.md §4.4](../AGENTS.md#44-core-code-vs-skill--the-decision-rule) decision rule per concern; default is "leave it in the skill".
- Wire `import-linter`, `ruff`, `pyright` into CI.
