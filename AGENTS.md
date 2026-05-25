# AGENTS.md — charter-agent

> Read this before every change. It is the operating contract for any coding agent (GitHub Copilot, Claude, Cursor, etc.) working in this repository. Humans should also read it before opening a PR.

This file follows the [agents.md](https://agents.md) convention. Sections 1–10 are the contract (invariants, tech choices, conventions, change-safety checklist). Section 11 is a consolidated build-time reference (env vars, action verbs, skills, schemas, boot sequence, MCP/Toolbox shape, client contract, test matrix, dependencies, phases) so a builder doesn't have to scroll the 700-line architecture doc to wire a single module. The architecture doc remains authoritative — if anything in §11 drifts from it, fix one or the other in the same PR.

---

## 1. What this project is

An **agent-orchestrated project coordination workspace**. A senior coordinator (Chief of Staff, programme manager, deal lead, audit lead) describes a cross-functional deliverable in natural language. A Microsoft Foundry hosted agent decomposes it into a **Project Charter**, kicks off the workstreams across Microsoft 365 (SharePoint, Teams, email, Outlook tasks), watches for deliveries across heterogeneous channels, infers status, drafts nudges and reassignments for human approval, and consolidates the final artifact.

A **desktop client** (today: the spike under [`spike/desktop_to_foundry/`](spike/desktop_to_foundry/)) is the client surface. It signs the user in interactively, attaches their bearer to the agent's `/responses`, and renders the streamed output.

The **canonical scenarios** are listed in [functional-specs/project_workspace_spec.md §2.4](functional-specs/project_workspace_spec.md). A sample meeting-notes file is bundled under [`test-fixtures/`](test-fixtures/) purely as **one** test input for the first end-to-end run — the agent must handle arbitrary projects, and nothing in the spec, architecture, or code should treat that sample's project name, owners, sections, or shape as fixed. Treat it the way you would a unit-test fixture.

---

## 1.5 Current implementation status (May 25, 2026)

The MVP ships as a **one-skill-does-all** codebase with a multi-skill routing layer that is ready for additional skills. Read the rest of this document as the *design north star*; consult this section for what actually exists in the repo today.

**What ships:**

- **Responses-protocol surface only.** The agent serves `/responses` via `agent-framework-foundry-hosting.ResponsesHostServer`; there is no orchestrator, no verb dispatcher, no `/invocations`. The client POSTs `{input, previous_response_id?}` and streams the SSE event sequence. The host model picks tools from the prompt directly.
- **Multi-skill routing.** `runtime/foundry_host.py` builds one warm MAF `Agent` per loaded skill at boot. `runtime/responses_host.py`'s `_ResilientResponsesHostServer` pre-routes each request: it parses the `[charter-agent-context: project_id=… is_new=… skill=…]` preamble, then reads `project_log["skill"]` for resume turns, and swaps `self._agent` to the correct warm Agent before invoking `ResponsesHostServer`. Adding a new skill requires only a new `agent/skills/*/SKILL.md` — no code changes.
- **One skill**: [`agent/skills/sow-response/SKILL.md`](agent/skills/sow-response/SKILL.md) (v0.5). End-to-end SOW-response workflow — first-run and resume modes. Generic across customers/projects; per-engagement variety lives in the Charter it reasons against. References: [`references/OUTPUT_FORMAT.md`](agent/skills/sow-response/references/OUTPUT_FORMAT.md) (closing receipt + dashboard protocol), [`references/COMMUNICATION_MATRIX.md`](agent/skills/sow-response/references/COMMUNICATION_MATRIX.md), [`references/CLASSIFICATION_RUBRIC.md`](agent/skills/sow-response/references/CLASSIFICATION_RUBRIC.md), [`references/SOW_SECTIONS.md`](agent/skills/sow-response/references/SOW_SECTIONS.md).
- **Domain tools for `sow-response`** (in [`skills/sow_response/tools.py`](agent/src/charter_agent/skills/sow_response/tools.py)): `load_project_state`, `stamp_project_skill`, `start_charter`, `add_charter_task`, `record_kickoff`, `record_submission`, `mark_task_polled`, `record_nudge_sent`, `dashboard_payload`, `publish_view`. These own the on-disk shape, status math, and idempotency guards. `load_project_state` is a pure read; the sidebar stub write is handled by the explicit `stamp_project_skill` call.
- **Generic state tools** exposed to the host model via MAF `@tool` (in [`runtime/state_tools.py`](agent/src/charter_agent/runtime/state_tools.py)): `state_read_text` / `state_write_text` / `state_read_json` / `state_write_json` / `state_file_exists` / `state_list_files` / `log_workflow_step`. The skill drives all `$HOME` I/O through these.
- **State files** the `sow-response` skill writes/reads (none enforced by Pydantic — the skill body owns the shape): `project_charter.md` (charter is markdown, not JSON), `project_log.json` (tasks, submissions, cursors, status, `skill` field), `activity.json` (append-only NDJSON), `agent_session/<session-id>.json` (MAF thread).
- **`runtime/project_router.py`** — strips the `[charter-agent-context: project_id=… is_new=… skill=…]` preamble from every incoming message, calls `state.set_active_project_id()`, and exposes the parsed skill name via `last_routed_skill()`. The skill never sees the preamble.
- **`spike/desktop_to_foundry/chat.py`** — reference CLI client. Sends the context preamble on every turn (`--project-id`, `--skill`); implements the silent empty-completion recovery from §9.1 (retry with `previous_response_id=None`, same `agent_session_id`).
- **`runtime/foundry_host.py`** — MAF `Agent` + `MCPStreamableHTTPTool` against the `Charter-Agent-Tools` Foundry Toolbox v1 (135 tools across 8 WorkIQ servers). The Toolbox-channel bearer is `DefaultAzureCredential` → `https://ai.azure.com/.default` (Foundry MI in prod; dev `az login` identity locally). Per-user identity propagation into MCP calls is the Foundry platform's responsibility via OAuth Identity Passthrough on the Toolbox connections — the agent process holds no user tokens (see [invariant 3](#3-non-negotiable-architectural-invariants)).

**What the rest of this contract describes that does *not* exist yet:**

- The per-domain modules `charter/`, `kickoff/`, `capture/handlers/*`, `status/`, `actions/`, `consolidation/`.
- The eight-skill split (`project-kickoff`, `status-refresh`, `capture-classify`, `compliance-check`, `draft-outbound`, `consolidate`, `amend-charter`, `render-dashboard`).
- The nine-verb invocation contract (`propose_charter`, `ratify_charter`, `render_dashboard`, `execute_suggested`, `dismiss_suggested`, `amend_charter`, `override_capture`, `coordinator_chat`, `close_project`).
- Pydantic schema models (`charter/schemas.py`). State shape is owned by the skill body today.
- The `workiq/` package is a placeholder; skills call WorkIQ tools directly through the Toolbox.

The detailed module/skill/verb layouts in §5, §11.2, §11.3, §11.4, and §11.10 describe a *possible evolution* if specific concerns prove to need bit-exact Python per the [§4.4](#44-core-code-vs-skill--the-decision-rule) decision rule. **Do not assume any of that machinery exists when writing a change.** When in doubt, check `agent/src/charter_agent/` and `agent/skills/`.

---

## 2. Read these before touching code

In this order, every time you start a non-trivial change:

1. [functional-specs/project_workspace_spec.md](functional-specs/project_workspace_spec.md) — the requirements / "what & why" contract.
2. [architecture/architecture_and_design.md](architecture/architecture_and_design.md) — the "how", with concrete contracts, schemas, and module boundaries.
3. [functional-specs/references.md](functional-specs/references.md) — every external doc the design is grounded in.
4. This file — for the invariants and conventions that apply across all changes.

If your change contradicts any of these, **update the spec first** (in the same PR) and call it out in the description. Quiet drift from the spec is the single most common way this kind of system rots.

---

## 3. Non-negotiable architectural invariants

These exist because violating them breaks the system's identity, security, or scaling model. Do not work around them. If you think you have a case for breaking one, raise it explicitly — don't slip it in.

1. **Skills-first, conformant to the open [agentskills.io](https://agentskills.io/specification) spec.** This is the most load-bearing architectural choice in the system, above every other invariant on this list. Every reusable agent capability — classify, draft, validate, consolidate, propose, render — is packaged as an **Agent Skill**: a folder under `agent/skills/{name}/` containing a `SKILL.md` with valid YAML frontmatter (required `name` and `description`; optional `metadata`, `license`, `compatibility`, `allowed-tools`), plus optional `scripts/`, `references/`, `assets/` subdirs. Skills are progressively disclosed (name+description always loaded at boot, body on activation, supporting files on demand). A small in-repo loader (`runtime/skill_loader.py`, ~50 lines) reads every `agent/skills/*/SKILL.md`, validates the frontmatter, and injects the body into the MAF host `Agent`'s instructions / tool-selection surface at boot. Conformance to the open spec buys us portability (the same skill can be loaded by Claude Code, VS Code, Goose, Gemini CLI, Kiro, fast-agent, GitHub Copilot etc. for isolated authoring/testing), auditability (skills are versioned files reviewed in PRs), and a clean cut between *deterministic plumbing* (code) and *reasoning/generation/judgement* (skill). See [§4.3](#43-agent-skills-format-agentskillsio-conformance) for the format contract and [§4.4](#44-core-code-vs-skill--the-decision-rule) for the heuristic. Note: agentskills.io is a *format* spec, not a runtime or tool-discovery mechanism — Claude-Code-specific concepts like `plugins/` and `mcp.json` are **not** part of it and **not** used here; tool discovery for us is the Foundry Toolbox over MCP (see [§4.1](#41-foundry-toolbox-via-native-maf-mcpstreamablehttptool)).
2. **Generic over specific (the corollary of invariant 1).** The agent's *code* is project-shape-agnostic. *All* per-project variety lives in (a) the **Project Charter** (`$HOME/project_charter.md` in the session sandbox — a Markdown document the skill emits at kickoff) and (b) the **Agent Skills** under `agent/skills/` (auto-loaded by the host runtime; shape the agent's reasoning declaratively per workflow). Do not hard-code domain logic — board pack, audit, escalation, budget, etc. — anywhere in the agent itself. If a feature seems to need it, the answer is almost always "extend the Charter, or add or refine a skill."
3. **WorkIQ identity is propagated by the Foundry platform, not by the agent.** The desktop client authenticates the end user, attaches their bearer to `/responses` (scope `https://ai.azure.com/.default`), and the Foundry runtime exchanges that identity into a WorkIQ token internally per Toolbox connection — emitting an `oauth_consent_request` SSE event the first time per (user, connection) so the user can consent in a browser. The agent process holds no WorkIQ refresh tokens, signs no JWTs, and runs no OBO flow. Application-only auth is not supported by WorkIQ ([spec §10.1](functional-specs/project_workspace_spec.md)). An earlier design held a SOW-Owner delegated refresh token in the agent and ran confidential-client OBO server-side (`runtime/workiq_token.py`, `SOW_OWNER_OBO_*` env vars, custom app reg); that plan was abandoned in May 2026 because the microsoft.com tenant blocks admin consent for `sansri@microsoft.com` and the WorkIQ resource apps in the tenant expose zero `oauth2PermissionScopes` (they are client apps, not resource APIs). The dead modules were removed in the May 22 cleanup — see [`spike/desktop_to_foundry/README.md`](spike/desktop_to_foundry/README.md) and [`architecture/architecture_and_design.md` §7](architecture/architecture_and_design.md).
4. **No background workers, no cron, no schedulers.** The agent only runs when a user hits `/responses`. There is no autonomous wake-up loop. Anything that *feels* like it needs scheduling is wrong — re-think it as "runs on next visit."
5. **Human-in-the-loop for every outbound action.** The agent drafts; the user approves; the agent sends in the **user's identity** (the bearer the client attached to `/responses`, propagated by the Foundry platform per invariant 3 — not as a bot). There is no `auto_approve` mode. Do not add one.
6. **State lives in `$HOME`, period.** No external database, queue, cache, or event bus. The Foundry per-session microVM `$HOME` (today: `project_charter.md`, `project_log.json`, `activity.json`, `agent_session/<id>.json`) is the entire project store. The desktop client is a renderer; it holds no state.
7. **Charter immutability outside the ratification flow.** Only the kickoff and amendment code paths (today: the corresponding sections of `sow-response/SKILL.md`; planned: dedicated `kickoff` and `amend_charter` verbs) may write `project_charter.md`. Both must run through coordinator ratification. Track a Charter version (today: in `project_log.json`; future: as Pydantic field) and bump on every amendment.
8. **Use the Responses protocol.** The agent serves the OpenAI-compatible Responses protocol (`/responses` + SSE), hosted by `agent-framework-foundry-hosting.ResponsesHostServer`. The host framework owns conversation history via `previous_response_id`; the agent constructs a single MAF `Agent` with the `sow-response` skill body as instructions and hands it to the server. Don't reintroduce the Invocations protocol or a verb-dispatch orchestrator.
9. **Channel-watchers are a registry — *when* code-side channel handlers exist.** Today there is no `capture/handlers/*` registry — channel polling is owned by `sow-response/SKILL.md` §9, which drives it via the WorkIQ Mail/Teams tools through the Toolbox. The registry rule applies *if and when* channel-handling is lifted into Python: new `watch_channel.kind` values must plug into the registry described in [architecture §6.2](architecture/architecture_and_design.md). Never `if channel.kind == "...":` switches scattered in agent code.
10. **Idempotency on every outbound side-effect.** Every suggested action must carry a UUID and a guard that prevents double-execution. Today the skill body owns the guard (deduping submissions by `internetMessageId` in `project_log.json`); the planned `actions/` module would carry a structured `state.executed_action_ids` set. A double-approve must not double-send.
11. **No ports exposed from the sandbox.** The agent's only public surface is the Foundry-gated `/responses` endpoint. The desktop client reaches it through the platform; nothing else in this repo listens on a port.
12. **Single runtime: MAF `Agent` on Foundry.** The agent has exactly one runtime: a Microsoft Agent Framework `Agent` (from `agent_framework`, *not* `ChatAgent`) on a Foundry `gpt-5.x` deployment, authenticated by Managed Identity, served by `agent-framework-foundry-hosting.ResponsesHostServer` (which depends on `azure-ai-agentserver-responses`). It owns `/responses`, the session lifecycle (multi-turn via `previous_response_id`), Toolbox MCP tool dispatch (raw `MCPStreamableHTTPTool`), and every reasoning step — charter proposal, classification, drafting nudges, status triangulation, coordinator chat, consolidation — driven declaratively by the `sow-response` skill body loaded as instructions. (Note: `agent-framework-core` 1.4.x exposes the host-agent class as `agent_framework.Agent` — `ChatAgent` does **not** exist at the package top level; constructor is `Agent(client=<FoundryChatClient>, instructions=..., *, tools=[...])`.)

    **No second runtime, no generated Python.** Earlier drafts of this contract called for a Copilot-SDK "codegen sub-agent" that would generate a per-project `$HOME/code/consolidator.py` for the `consolidate` skill. That was dropped (May 2026) — the operational complexity (second credential surface, silent backend-flip hazard, constructor-PAT trap, dual import-linter contracts) wasn't justified by the demo scenarios on the table. The `consolidate` skill instead writes the final deliverable declaratively via WorkIQ Word/SharePoint tool calls. Reintroducing a codegen sub-agent (Copilot SDK or otherwise) requires an ADR and an update to this invariant.

    **What this forbids.** Do not introduce a second runtime, second LLM path, or in-process code-generation step without an ADR. If the host model is insufficient for a specific step, surface another Foundry deployment as a **named MAF tool** that calls it with the agent's Managed Identity — do not stand up a parallel client.

### Skill-body discipline — the rule AI coding assistants violate most often

`SKILL.md` and `references/*.md` files are **model-runtime text**. The model executes them at inference time. A careless rephrasing, a deletion, or a well-intentioned "simplification" changes model behaviour in ways that cannot be caught by tests, linters, or type-checkers. These files must be treated with the discipline you would apply to a legal clause or a compliance policy — not to a code comment.

**Hard rules for any coding agent (Copilot, Claude Code, Cursor, etc.):**

1. **Never touch a skill file as a side effect of a code change.** Fixing a Python bug, refactoring a tool, updating a schema, or reorganising imports does not justify touching `SKILL.md` or any `references/*.md`. If the code change genuinely requires a corresponding skill update, make that a deliberate, named change in the same PR with an explicit justification.

2. **Never reword, shorten, or "clean up" a skill body for style.** What looks like cosmetic tidying is a semantic change. A sentence that seems redundant may be the one instruction that prevents the model from making a plausible-but-catastrophically-wrong decision.

3. **Domain rules in skill files are invariants, not suggestions.** Examples that must be preserved verbatim in intent:
   - *Two-source rule* — the RFP is the source of requirements; the meeting notes are the source of task owners. These must never be conflated. Customer email addresses in the RFP must never become task owner addresses.
   - *No fabrication* — if WorkIQ cannot ground the project, the agent stops and asks; it does not invent owners or requirements.
   - *UPN quoting* — collaborator email addresses are copied verbatim from the source; directory lookups are forbidden when an address is already present.
   - *Human-in-the-loop* — the agent drafts nudges; it never sends without explicit SOW Owner approval.

4. **The `references/*.md` files have the same status as `SKILL.md`.** Do not modify `CLASSIFICATION_RUBRIC.md`, `COMMUNICATION_MATRIX.md`, `SOW_SECTIONS.md`, or `OUTPUT_FORMAT.md` as a side effect of any code change.

5. **Adding tool-API documentation to a skill body is wrong.** JSON discriminators, status codes, idempotency guarantees, path safety rules, and timeout notes belong in tool docstrings — not in `SKILL.md`. The skill body should read like a briefing to a knowledgeable human coordinator, not like a developer integration guide.

A longer, scenario-flavoured version of these invariants is in [spec §10 "Things easy to get wrong"](functional-specs/project_workspace_spec.md). Read it. **Invariant 1 (skills-first) is the one most likely to get quietly violated under time pressure** — every time you find yourself reaching for a `match` statement, an `if project_kind == "..."` branch, or a hard-coded prompt fragment inside `orchestrator.py` or any `*/handlers/*.py`, stop and ask whether it belongs in a skill instead.

---

## 4. Technology choices (locked)

| Layer | Choice | Notes |
|---|---|---|
| Host agent runtime | **Microsoft Agent Framework `Agent`** (`agent-framework-core` + `agent-framework-foundry`; top-level import is `agent_framework`; the host-agent class is `Agent`, not `ChatAgent`) on a Foundry `gpt-5.x` deployment via Managed Identity, served by [`agent-framework-foundry-hosting.ResponsesHostServer`](https://pypi.org/project/agent-framework-foundry-hosting/) (which depends on `azure-ai-agentserver-responses`) | Owns `/responses`, multi-turn via `previous_response_id`, raw `MCPStreamableHTTPTool` dispatch against the Toolbox. One warm `Agent` per process. The Responses server emits OpenTelemetry traces and the OpenAI Responses SSE event stream automatically. |
| Agent hosting | **Foundry hosted agents** (Responses protocol) | **Stateless from the agent's POV** — history is replayed by the host server via `previous_response_id` on every call. |
| Host model | **Foundry `gpt-5.x` deployment** via Managed Identity (`DefaultAzureCredential`) | Every reasoning verb. `AZURE_AI_MODEL_DEPLOYMENT_NAME` is the deployment name. In the dev Foundry project (`ocvp-agent-svc`) the available deployments are `gpt-5.2`, `gpt-5.2-codex`, `gpt-5.3-chat`, `gpt-5.4`, `gpt-4.1-mini`, `gpt-4o`, `sora-2`, `gpt-image-1.5` — **plain `gpt-5` is not deployed**, so the dev `.env` pins `gpt-5.4`. Verify with `az cognitiveservices account deployment list -n ocvp-agent-svc-resource -g pcdotai-agent` if changing. |
| Per-project specialisation | **Agent Skills** in `agent/skills/*/SKILL.md` (loaded at process start by `runtime/skill_loader.py`) | Sole mechanism. Skills are declarative, transparent, auditable, agentskills.io-conformant. The skills set is generic across projects; per-project variety comes from the Charter the skills reason against. There is no per-project generated code path. |
| M365 data plane | **WorkIQ MCP servers**, identity propagated by Foundry OAuth Identity Passthrough | The end-user's bearer (attached to `/responses` by the client) is exchanged by the Foundry runtime per Toolbox connection — see [invariant 3](#3-non-negotiable-architectural-invariants). Application-only is not supported by WorkIQ. |
| Bundling multiple MCP servers | **Foundry Toolboxes** (MCP-compatible endpoint), consumed by the host runtime as a raw MAF `MCPStreamableHTTPTool` | All WorkIQ MCP servers are bundled in a Foundry Toolbox named `Charter-Agent-Tools`. The host runtime declares a single `MCPStreamableHTTPTool` against the Toolbox URL with a `header_provider` that stamps the bearer + `Foundry-Features: Toolboxes=V1Preview` header; MAF handles `initialize` / `tools/list` / `tools/call` plumbing. See [§4.1](#41-foundry-toolbox-via-native-maf-mcpstreamablehttptool), [references.md §8](functional-specs/references.md). The portal-generated `samplecode_toolbox.py` is reference-only — its hand-rolled `McpBridge` is what `MCPStreamableHTTPTool` replaces. |
| Client | **Desktop client** (today: [`spike/desktop_to_foundry/`](spike/desktop_to_foundry/)) | The client surface. Acquires the user token locally via MSAL public client (or `az login` for dev), POSTs to `/responses` with `Authorization: Bearer <user_token>`, opens the `oauth_consent_request` URL in a browser on first call per connection, then retries with `previous_response_id` to resume. |
| Identity | **Entra ID** — Foundry-assigned Agent ID (Managed Identity) for host model + Toolbox-channel auth; end-user delegated tokens are passed through to WorkIQ by the Foundry platform (invariant 3). The desktop client uses the Azure CLI public client (`04b07795-…`) or any pre-consented public client — no custom app registration. | Conditional Access surfaces naturally through the `oauth_consent_request` flow. |
| Observability | **Foundry agent tracing** (preview for hosted agents) — server-side spans (root `/responses` request, model calls, MCP `tools/list` / `tools/call`) emitted automatically by Foundry once App Insights is connected; custom spans via the `@trace_function` decorator re-exported from `azure.ai.projects.telemetry`. Plus `$HOME/activity.json` (audit, not telemetry). | Both, always. No manual OTel exporter wiring (the platform injects `APPLICATIONINSIGHTS_CONNECTION_STRING`), no hand-rolled span context managers, no `configure_azure_monitor` call. Process-wide attributes (`project.id`, `gen_ai.conversation.id`) are injected by one `SpanProcessor.on_start` at boot. **`AIProjectInstrumentor().instrument()` is deliberately NOT called** — `azure-ai-projects` 2.0.1/2.1.0's Responses instrumentor wraps the upstream stream in an `AsyncStreamWrapper` missing the `.headers` attribute `agent_framework_foundry` reads, crashing every turn. Re-enable when fixed upstream. |

Do **not** substitute these without an architectural decision record committed alongside the change.

### 4.1 Foundry Toolbox via native MAF `MCPStreamableHTTPTool`

The host runtime is MAF, and the Toolbox is attached as a raw `MCPStreamableHTTPTool` (from `agent_framework._mcp`) pointed at the Foundry Toolbox MCP endpoint. There is no `AzureAIProjectToolbox`-style wrapper in any shipped MAF package — the `agent-framework-azure-ai` distribution on PyPI (rc5/rc6 as of this writing) is broken against every released `agent-framework` core, and the working Foundry provider is `agent-framework-foundry` (`FoundryChatClient`, `FoundryAgent`), which intentionally exposes no Toolbox helper. Raw MCP is the production path, not a fallback.

What the runtime must do — treat each as a hard requirement, not a suggestion:

- Construct one `MCPStreamableHTTPTool(name="workiq", url=<toolbox-url>, header_provider=<callable>, http_client=<auth-injecting AsyncClient>, approval_mode="never_require", request_timeout=90, load_prompts=False)` per process. The URL is `{FOUNDRY_PROJECT_ENDPOINT}/toolboxes/{name}/versions/{version}/mcp?api-version=v1` in dev (pinned `TOOLBOX_VERSION`, e.g. `"1"`) and `{FOUNDRY_PROJECT_ENDPOINT}/toolboxes/{name}/mcp?api-version=v1` in prod (consumer endpoint, no version).
- Authenticate via `DefaultAzureCredential` against the `https://ai.azure.com/.default` scope (Foundry-assigned Managed Identity in production; the developer's `az login` identity locally). Tokens are minted per-call inside the auth-injection hook so they refresh naturally.
- The mandatory `Foundry-Features: Toolboxes=V1Preview` HTTP header **must** be stamped on every outbound Toolbox request, **and so must the `Authorization` bearer**. MAF's `header_provider` parameter ONLY fires on `call_tool` — not on `connect()` / `initialize` / `tools/list` — so it cannot stand alone for auth. **Pass an explicit `httpx.AsyncClient` whose `event_hooks={"request": [...]}` re-mints the bearer and stamps both headers on every request.** Set `header_provider` as well so per-tool-call headers remain a viable override path, but auth must live in the event hook.
- The Foundry Toolbox does **not** implement MCP `prompts/list`. Pass `load_prompts=False` when constructing `MCPStreamableHTTPTool` or `connect()` will fail with HTTP 400 inside MAF's load-prompts step.
- Transitive deps not pulled by the MAF distros — declare them in `pyproject.toml`: `mcp` (used by `streamable_http_client`) and `aiohttp` (used by `azure-ai-projects` async pipeline).
- The MCP `initialize` / `tools/list` / `tools/call` plumbing is owned by `MCPStreamableHTTPTool`; do not re-wire it.
- `approval_mode="never_require"` for WorkIQ reads **and** writes. Approval is enforced at a higher layer (today: by the skill body refusing to call write tools without an explicit user OK in the prompt; planned: a dedicated `actions/` module's `SuggestedAction` lifecycle, gated by the coordinator in the dashboard) — see [references.md §8](functional-specs/references.md).
- The Toolbox-channel `Authorization` bearer (the one the event hook stamps) is the **Foundry agent's Managed Identity** token, not a user token. User identity propagation into the MCP call itself is done by the Foundry platform via OAuth Identity Passthrough on the Toolbox connections (invariant 3) — the agent does not source, mint, or forward user tokens.
- Watch the 100-second non-streaming MCP call timeout in capture loops — fan out channel polls concurrently (`asyncio.gather`), not serially.

**Host model.** Wire the host `Agent` with `FoundryChatClient(project_endpoint=..., model=<deployment>, credential=DefaultAzureCredential())` from `agent-framework-foundry` (the `model` kwarg is the deployment name, e.g. `gpt-5.4` — there is no `model_deployment_name` kwarg), then construct `Agent(client=<that FoundryChatClient>, instructions=<skill body>, tools=[<MCPStreamableHTTPTool>])` from `agent_framework`. The class is `Agent`, not `ChatAgent` — the latter does not exist at the `agent_framework` top level in 1.4.x. Do **not** depend on `agent-framework-azure-ai` — it is broken on PyPI (its rc5/rc6 imports `BaseContextProvider`, renamed to `ContextProvider` in every released core; adding it back will reproduce the original install failure).

**Reference wire shape.** The portal-generated `architecture/samplecode_toolbox.py` (hand-rolled `McpBridge` with explicit `initialize` / `mcp-session-id` / `notifications/initialized` / streamed `tools/call` / tool-name sanitisation `.`/`-` → `_`) is exactly what `MCPStreamableHTTPTool` owns for us. Keep the sample file in the repo as a debugging aid, but no production code path imports it.

**Standing rule.** Microsoft Learn's "Connect agents to MCP servers" + "Create and use a Foundry Toolbox" are the authoritative sources ([references.md §8](functional-specs/references.md)). For anything beyond what's documented there — a new tool, an unfamiliar schema, an MCP protocol-version bump — introspect the **live Toolbox endpoint on the fly** rather than coding to a stale snapshot.

### 4.2 Model assignment policy

There is **one** model path:

| Path | Runtime | Model | Credential | Used for |
|---|---|---|---|---|
| **Host** | MAF `Agent` (`agent_framework.Agent`) | Foundry `gpt-5.x` deployment (dev pin: `gpt-5.4`) | Foundry-assigned Managed Identity (`DefaultAzureCredential`) | Every reasoning verb: charter proposal, classification, status triangulation, draft-outbound, coordinator chat, consolidation. |

**Credentials at deploy time:**

- `AZURE_AI_MODEL_DEPLOYMENT_NAME` is the host model deployment (`gpt-5.x`; the dev `.env` pins `gpt-5.4` because plain `gpt-5` is not a deployment on the `ocvp-agent-svc` project). Required at boot.
- `FOUNDRY_PROJECT_ENDPOINT` is auto-injected by the platform; used both for the host model and for the Toolbox.

**Things this rule forbids:**

- Do **not** spawn a second LLM path (a separate Anthropic API key, a Copilot SDK client, a different OpenAI account, a parallel reasoning helper) without an ADR and an update to invariant 12. If a skill needs a different model for a specific step, expose it as a **named MAF tool** that calls another Foundry deployment with the agent's Managed Identity — do not create another runtime.

**Where this is enforced in code:**

- `agent/src/charter_agent/runtime/foundry_host.py` is the **only** module allowed to instantiate the host `Agent` and `FoundryChatClient`. Enforced via `import-linter` contract.

### 4.3 Agent Skills format ([agentskills.io](https://agentskills.io/specification) conformance)

Every skill in `agent/skills/` must be a valid Agent Skill per the open spec. This is non-negotiable (invariant 1). Conformance gives us portability across any agentskills.io-compatible client and validation via the upstream [`skills-ref validate`](https://github.com/agentskills/agentskills/tree/main/skills-ref) tool, which CI must run on every PR that touches `agent/skills/`.

**Directory layout** (per skill):

```
agent/skills/{skill-name}/
├── SKILL.md          ← required: YAML frontmatter + Markdown instructions
├── scripts/          ← optional: helper scripts the skill may run
├── references/       ← optional: detail docs loaded on demand (REFERENCE.md, FORMS.md, …)
└── assets/           ← optional: templates, schemas, fixtures
```

**`SKILL.md` frontmatter** — required fields `name` and `description`; everything else optional:

```yaml
---
name: capture-classify                       # required; 1–64 chars; lowercase a–z, 0–9, hyphens; must equal parent dir; no leading/trailing/consecutive hyphens
description: Classifies a CandidateEvent emitted by a channel handler as submission, revised_submission, question, supporting_material, or unrelated relative to a Charter task. Use whenever the capture loop has a new event to label.   # required; 1–1024 chars; describe BOTH what the skill does AND when to use it; include trigger keywords
license: Proprietary                         # optional
compatibility: Requires MAF Agent runtime; Foundry Toolbox MCP endpoint   # optional; only if there are real environment requirements
metadata:                                    # optional; arbitrary string→string map
  owner: charter-agent
  version: "1.0"
allowed-tools: ToolboxMcp(workiq_files_*) ToolboxMcp(workiq_ask)   # optional, experimental; we use it where it tightens the safety surface
---
```

**Progressive disclosure rules** (the body of `SKILL.md` is loaded only when the skill activates — keep it under ~500 lines / ~5 000 tokens; move long material into `references/*.md` and link with relative paths like `references/COMPLIANCE_RULES.md`):

1. **Discovery** (always loaded at boot): `name` + `description` only — ~100 tokens per skill.
2. **Activation** (loaded when the description matches the task): full `SKILL.md` body.
3. **Execution** (loaded on demand): individual files in `scripts/`, `references/`, `assets/`.

**Repo conventions on top of the spec:**

- Skill names use the kebab-case verbs we already have: `project-kickoff`, `status-refresh`, `capture-classify`, `compliance-check`, `draft-outbound`, `consolidate`, `amend-charter`, `render-dashboard`.
- The `description` field is the *only* mechanism by which the orchestrator and the host runtime route work to a skill — invest time in it. Bad descriptions are the most common reason a skill is silently ignored at runtime.
- File references in `SKILL.md` are relative paths from the skill root (`scripts/extract.py`, `references/RUNBOOK.md`). Keep references one level deep — no nested chains.
- A skill is **never** project-specific. If a skill seems to need per-project text, the per-project text belongs in the Charter (`project_charter.md`) and the skill reads it from there.
- Skills are reviewed and shipped with the agent container image. There is no "hot-load a skill from $HOME" path, and no per-project generated code path either (see invariant 12).

**Out of scope / explicit non-goals here:**

- Claude-Code-specific concepts (`plugins/`, `mcp.json`, slash commands) are **not** part of agentskills.io and **not** used. Tool discovery is via the Foundry Toolbox over MCP ([§4.1](#41-foundry-toolbox-and-the-copilot-sdk-mcpbridge)).
- No external skill marketplace integration in MVP. "Portability" here means "a contributor can validate a skill locally in Claude Code or VS Code before opening a PR," not "users install third-party skills into a running project."

### 4.4 Core code vs skill — the decision rule

The single most common design question on this project will be "does this new functionality go into a `.py` module or into a new/existing skill?" The rule:

| Concern | Put it in | Why |
|---|---|---|
| Deterministic plumbing — atomic file I/O, JSON serialisation, schema validation, HTTP/MCP transport, request dispatch, OBO header propagation | **Code** (`agent/src/charter_agent/…`) | Invariants must hold byte-for-byte; not negotiable by an LLM. |
| State-of-the-world bookkeeping — atomic `$HOME` reads/writes, NDJSON append to `activity.json`, channel cursors, idempotency-gate sets | **Code** (today: `state.py` low-level helpers + `runtime/state_tools.py` `@tool` wrappers driven by the skill; planned: `actions/` for the gating set and `capture/handlers/*` for cursor mechanics) | Idempotency and audit integrity (invariants 10, 6) require strict, testable code where the data crosses Python module boundaries; while everything is one skill, the skill body is responsible. || Boot-time policy — env-var assertions, warm `Agent` lifecycle, MAF `MCPStreamableHTTPTool` wiring, OTel wiring | **Code** (`runtime/foundry_host.py`, `__main__.py`) | One-shot startup contract; no language-model judgement involved. |
| Verb dispatch — mapping `/invocations` action verbs to skill invocations + downstream effects | — (removed) | The agent serves `/responses` only; the host model picks tools directly from the prompt. There is no verb dispatcher. |
| Channel-handler poll mechanics — "since" cursors, dedup keys, author filters | **Skill today** (`sow-response/SKILL.md` §9, via WorkIQ Mail/Teams tools); **code if/when extracted** (would live in `capture/handlers/*`) | Currently the skill body owns cursor advancement and dedup. Promote to code only if cursor correctness starts failing or a non-skill consumer needs the same mechanics. |
| Classification, drafting, judgement, summarisation, gap detection, prompt-generation, document consolidation orchestration, coordinator conversation | **Skill** (`agent/skills/*/SKILL.md`) | These benefit from natural-language instructions, reference docs, and iteration without code review cycles; they are what LLMs do well. |
| Per-workflow procedural knowledge ("how a kickoff is done," "what makes a board-pack section compliant," "how to phrase a nudge respectfully") | **Skill** (with supporting `references/*.md`) | Captured once, version-controlled, swappable per organisation by editing one file. |
| Cross-section reconciliation and final-deliverable assembly | **Skill** (`sow-response` §5–§7 consolidation flow today; future dedicated `consolidate` skill if extracted) | The skill instructs the host model to stitch sections together and emit the deliverable via WorkIQ Word/SharePoint tool calls. If a future scenario genuinely needs deterministic Python (e.g. strict numeric cross-section reconciliation against a template the model can't get right on its own), promote the requirement to an ADR and ship the reconciliation as a fixed in-repo module under `consolidation/`. |

**The acid test for any new feature:** if you can describe the behaviour fully in English to a colleague in under 200 words, it should be a skill. If you can't — because it has bit-exact invariants, performance constraints, or security gates — it's code. When in doubt, prefer the skill; you can always lift bits down into code later if they prove non-negotiable.
**Skill prompts as the schema contract.** Whenever a skill emits a Pydantic-validated artifact (e.g. `propose_charter` returning a `Charter`), do **not** reach for OpenAI strict structured output (`response_format=<PydanticClass>` / `client.responses.parse`). Strict mode demands `additionalProperties: false` on every object node and every property in `required` — which is incompatible with our schemas (open `dict[str, Any]` configs, many optional fields). Instead: emit JSON via the skill body, strip ```json fences, validate with `Model.model_validate_json()`. The SKILL.md output contract then becomes the binding spec; enumerate explicitly (a) every `Literal[…]` enum's allowed values, (b) the expected key set for any `dict`/object field, (c) any `str` field a model might naturally emit as a structured value (say "plain string" with an example). The model honors precise wording; vague descriptions burn iterations chasing `ValidationError`s.
---

## 5. Repository layout (target)

The repo is currently spec-only. As code lands, follow this layout. Update this section in the same PR if you add a new top-level directory.

```
charter-agent/
├── AGENTS.md                          ← you are here
├── README.md                          ← human-facing intro (TBD)
├── functional-specs/                  ← the "what & why" + references (normative)
│   ├── project_workspace_spec.md
│   └── references.md
├── test-fixtures/                     ← sample inputs + reviewer mocks (NON-normative)
│   ├── README.md
│   ├── sample-meeting-notes.md        ← one sample input for end-to-end tests
│   ├── dashboard-mock.html            ← illustrative dashboard render for the sample
│   ├── dashboard-mock-alt.html        ← earlier/alternate mock kept for comparison
│   └── dashboard-sample.png           ← screenshot reference
├── architecture/                      ← the "how"
│   ├── architecture_and_design.md
│   └── samplecode_toolbox.py          ← portal-generated Toolbox + Copilot-SDK reference
├── agent/                             ← the Foundry hosted agent
│   ├── Dockerfile
│   ├── agent.yaml
│   ├── pyproject.toml
│   ├── scripts/                       ← dev helpers + live smokes (deploy, dev_run, smoke_responses, smoke_calendar)
│   ├── skills/                        ← Agent Skills (agentskills.io spec); auto-loaded at process start
│   │   └── sow-response/              ← THE skill (one-skill-does-all)
│   │       ├── SKILL.md               ← workflow body: §1 mode-detect, §2–§7 first-run, §8 resume, §9 capture, §10 must-NOT
│   │       └── references/            ← progressively-disclosed detail
│   │           ├── SOW_SECTIONS.md
│   │           ├── COMMUNICATION_MATRIX.md
│   │           └── CLASSIFICATION_RUBRIC.md
│   ├── src/charter_agent/
││   ├── __main__.py                ← boot entry; wires tracing, warms runtime, starts ResponsesHostServer
│   │   ├── runtime/
│   │   │   ├── foundry_host.py        ← sole owner of MAF Agent + AgentSession; MCPStreamableHTTPTool wiring
│   │   │   ├── skill_loader.py        ← reads agent/skills/*/SKILL.md, registers with Agent
│   │   │   └── state_tools.py         ← agent-side @tool wrappers around state.py (the skill drives these)
│   │   ├── orchestrator.py            ← 3-verb dispatcher: echo, list_tools, run_skill
│   │   ├── state.py                   ← $HOME read/write helpers (atomic; path-containment-checked)
│   │   ├── workiq/                    ← placeholder package; skills call WorkIQ via the Toolbox directly today
│   │   └── observability.py           ← `@trace_function`, owns `$HOME/activity.json` + process-attribute span processor
│   └── tests/
├── spike/
│   └── desktop_to_foundry/            ← the only client surface this sample ships
└── .github/
    └── workflows/                     ← CI: lint, type-check, test, build, deploy
```

**Aspirational layout (designed, not built):** the per-domain modules `charter/`, `kickoff/`, `capture/handlers/*`, `status/`, `actions/`, `consolidation/` and the eight-skill split under `agent/skills/`. Promote functionality into one of these only when the §4.4 decision rule says it's earned a Python home (bit-exact invariants, performance constraints, security gates) or when a second skill needs the same primitive.

Two folders that are explicitly *not* in the repo: a `database/` and a `worker/` directory. If you find yourself wanting either, see invariants 4 and 6.

---

## 6. Conventions

- **Language**: Python 3.12 for the agent. Type-hinted, `ruff` + `pyright` clean. No untyped public functions.
- **Tracing**: use the `@trace_function` decorator re-exported from `observability` for custom spans. Do not write `tracer.start_as_current_span(...)` by hand or build wrapper context managers — Foundry's instrumentor and the protocol server own the tracer provider and the App Insights exporter.
- **Audit log**: every state-mutating step calls `observability.log_activity(...)` to append to `$HOME/activity.json`. This is product behaviour (the narrative the dashboard renders), not telemetry. Never call `print()` or bare `logging.info` for things that belong in the audit log.
- **WorkIQ access**: today, skills call WorkIQ tools directly through the host-runtime-attached Foundry Toolbox (`MCPStreamableHTTPTool`). The `agent/src/charter_agent/workiq/` package is a placeholder kept for the eventual thin-wrapper layer; when it lands, the rule becomes "no direct MCP calls from any non-`workiq/` module — `workiq/` is the only consumer of the Toolbox tool surface." Per-user identity propagation into MCP calls is owned by the Foundry platform (invariant 3); the agent does not mint or forward user tokens.
- **Host runtime**: Only through `agent/src/charter_agent/runtime/foundry_host.py`. One MAF `Agent` instance per agent process (kept warm). One MAF `AgentSession` thread per Foundry session, keyed by `FOUNDRY_AGENT_SESSION_ID`, persisted to `$HOME` — never recreated within the same Foundry session.
- **Skills**: Project-workspace specialisation lives in `agent/skills/{name}/SKILL.md` and must be valid per the [agentskills.io spec](https://agentskills.io/specification) (see [§4.3](#43-agent-skills-format-agentskillsio-conformance)). Loaded at boot by `runtime/skill_loader.py` and injected into the host `Agent`. CI runs `skills-ref validate ./agent/skills/*` on every PR that touches the directory; PR is blocked on failure. Skill changes are code changes — reviewed, versioned, shipped with the agent image. Do not write per-project skills; the skills set is generic, the Charter is what the skills reason against. Before adding any new feature, run it through the decision rule in [§4.4](#44-core-code-vs-skill--the-decision-rule).
- **JSON schemas**: today, state-file shape is owned by `sow-response/SKILL.md` (see §11.4). When a file gains a second Python consumer or an external typed surface, promote it to a Pydantic model under a new `charter/schemas.py` module — the JSON shapes in [architecture/architecture_and_design.md §5](architecture/architecture_and_design.md) are the design north star at that point.
- **Tests**: every test that touches WorkIQ or external services must mock the boundary (`respx` for HTTP). Idempotency tests are required wherever a future outbound-action executor lands. Channel-handler cursor tests apply when `capture/handlers/*` exists.
- **No comments** explaining *what* the code does. Only comments for *why* something non-obvious is the way it is. See [spec §10.13](functional-specs/project_workspace_spec.md) — the audit log is the narrative record, not source comments.
- **Commit messages** should reference the spec section being implemented or changed (e.g. `kickoff: implement section 8.2 SharePoint channel handler`).

---

## 7. Build sequencing

The phases in [spec §9](functional-specs/project_workspace_spec.md) are the canonical order. Don't skip ahead — every phase produces a runnable artifact, and later phases depend on the earlier ones being demonstrably working. The 12-section MVP demo scope in [spec §12](functional-specs/project_workspace_spec.md) is the right first goal.

For each phase, the agent doing the work should:

1. Read the spec section for that phase.
2. Read the corresponding architecture section.
3. List the concrete files it will create/change before changing anything.
4. Implement.
5. Add tests covering at least the happy path plus one error / idempotency case.
6. Update the architecture doc if a contract changed.
7. Commit and stop. Don't fold subsequent phases into the same change.

---

## 8. The change-safety checklist (run before every commit)

**Skill-body discipline (read this first — it is the check AI assistants fail most often):**
- [ ] **Is this a code-side change that also touched `SKILL.md` or any `references/*.md`?** If yes, stop. Code fixes must not carry skill edits as a side effect. Make the skill change a separate, deliberate commit with an explicit justification.
- [ ] **Is the `SKILL.md` body still domain-first?** It must read like a briefing to a knowledgeable human coordinator — not like a developer integration guide. If it contains JSON discriminators, status codes, idempotency guarantees, path safety rules, or timeout notes, those belong in tool docstrings, not here.
- [ ] **Are the domain invariants intact?** Verify that the two-source rule (RFP = requirements only; meeting notes = owners only), the no-fabrication rule, the UPN-quoting rule, and the human-in-the-loop rule are all present and have not been softened or shortened. See the skill-discipline block in §3.
- [ ] **Are the `references/*.md` files untouched unless a deliberate domain update was intended?** `CLASSIFICATION_RUBRIC.md`, `COMMUNICATION_MATRIX.md`, `SOW_SECTIONS.md`, and `OUTPUT_FORMAT.md` are model-runtime text with the same status as `SKILL.md`.

**Architecture and code:**
- [ ] Does the change preserve invariants 1–12 in §3?
- [ ] **Skills-first check (invariant 1):** if the change adds reasoning, drafting, judgement, classification, or any procedural domain knowledge, has it been packaged as a skill (or as edits to an existing skill) rather than added to a `.py` module? Run it through the [§4.4](#44-core-code-vs-skill--the-decision-rule) decision rule.
- [ ] **agentskills.io conformance check:** if the change touches `agent/skills/`, does `skills-ref validate` pass? Are `name` and `description` populated, with the description including trigger keywords?
- [ ] If it adds a domain-specific behaviour, has it been moved into the Charter schema, an existing skill, or a new skill — *not* into orchestrator/handler/kickoff code?
- [ ] If it touches `$HOME`, does it route through `state.py` and emit an activity-log entry?
- [ ] If it calls WorkIQ, is the call going through the Toolbox `MCPStreamableHTTPTool` (and, when the wrapper layer lands, through `workiq/`)? Does it rely on the Foundry platform for per-user identity propagation (invariant 3) rather than minting or forwarding a user token from the agent?
- [ ] If it adds an outbound action that *will* be exposed via a typed `SuggestedAction` lifecycle, does it have a UUID and an idempotency check? (Today, idempotency is the skill's responsibility; when an `actions/` module lands, this becomes a hard check against `executed_action_ids`.)
- [ ] If it adds a new channel poll loop, has the polling logic been added to the skill body (current pattern) rather than a switch statement in code? When the `capture/handlers/*` registry lands, this becomes "register in the registry, never `if channel.kind == \"...\":` inline."
- [ ] If it adds a new public surface, is it expressed as a MAF tool on the warm `Agent` (not as a parallel protocol)?
- [ ] If it changed a state-file shape that `sow-response/SKILL.md` reads or writes, was the skill body (and any relevant `references/*.md`) updated in the same PR?
- [ ] Lint, type-check, and tests pass.

---

## 9. Operational notes worth surfacing

- **One session per project.** The desktop client sets `x-agent-chat-isolation-key = project_id` and `?agent_session_id=project_id` on every call. Each project gets one Foundry session → one `$HOME` → one MAF thread. Multiple users on the same project share the session; per-user WorkIQ data isolation is achieved by the Foundry platform's identity passthrough (invariant 3) rather than by per-user sessions.
- **50-concurrent-session preview limit per sub/region** translates to **50 active projects**. Track this as the portfolio scales.
- **`FOUNDRY_` env var prefix is reserved** by the platform and may be silently overwritten. Name internal env vars without that prefix — e.g. `TOOLBOX_MCP_ENDPOINT`, not `FOUNDRY_TOOLBOX_ENDPOINT`.
- **Build for `linux/amd64`.** Foundry hosted agents reject other architectures. Use `azd deploy` (ACR remote build) or `docker build --platform=linux/amd64 …` on Apple Silicon.

### 9.1 Responses-on-Foundry: the two-key model (load-bearing)

Single biggest source of confusion for any new feature that touches session lifecycle, retry, or resume. Read before editing `desktop-client/app.py::_post_one` or anything that mints / forwards IDs.

There are **two orthogonal identifiers** on every Responses turn:

| Key | Backed by | Lifetime | Loses on |
|---|---|---|---|
| `agent_session_id` (body `extra_body={"agent_session_id": …}`) | **Persistent Foundry microVM + `$HOME`** (`project_charter.md`, `project_log.json`, `activity.json`, `agent_session/<id>.json`) | Up to 30 days; 15-min idle deprovisions compute but preserves state | Foundry idle-reap (>30d or platform GC) |
| `previous_response_id` (body field) | **In-memory transcript store** in the Responses host (`InMemoryResponseProvider`) — message history only | Process lifetime of the agent container | Container restart (deploy, scale-in, crash) |

The microVM survives container restarts within the 30-day window; the in-memory transcript does not. **Never conflate them.**

**Silent empty-completion failure mode.** When the client sends a `previous_response_id` whose transcript has rolled, the hosted endpoint returns **200 OK** with `response.created` → `response.in_progress` → `response.completed` and zero `output_text` / `function_call` events. No error, no exception. The UI looks hung. Easy to misdiagnose as a network or auth issue — it is neither.

**Correct recovery (in `_post_one`):** on stream-done, if `previous_response_id` was sent AND no consent payload AND no final text AND event count ≤ 4, retry the same prompt with `previous_response_id=None` and **the same `session_id`**. Clearing `session_id` in the retry orphans the user's microVM and forces a `first_run`, silently losing the entire sandbox pointer.

**Server-side has no role here.** The Foundry agentserver mounts the right per-session `$HOME` before our code runs. `runtime/state_tools.py` just reads `state.home_dir()`. Do not add session-handling code in the agent runtime; the client is solely responsible for sending the right `agent_session_id` on every POST.

### 9.2 Cross-endpoint isolation: local and hosted are different worlds

Every project is keyed by `(mode, project_id)` where `mode ∈ {local, hosted}`. The two modes share **nothing** — separate agent process / microVM, separate `$HOME`, separate `session_id`, separate `previous_response_id`. The same human project name can exist in both worlds with independent state.

**Required client behavior on endpoint switch** (`set_mode` + UI mode handler):

1. Clear transcript DOM, activity DOM, `currentAgentMsg`, `shownPhaseCards` (via `clearTranscript()` / `clearActivity()`).
2. Reset transient IDs (`self.session_id = None; self.previous_response_id = None`); they're then reloaded from the new mode's active project via the `_current` property.
3. Re-bind active project from `projects[mode][active_pid]`.

Skip (1) → previous endpoint's transcript bleeds into the new view. Skip (2) → stale `session_id` from the other mode gets sent to the new endpoint → server rejects → UI hangs ("shimmer then nothing").

### 9.3 Where project state actually lives — local vs hosted asymmetry

- **Local mode**: client reads `<AGENT_HOME>/projects/<pid>/project_log.json` directly from the local filesystem (`desktop-client/app.py::_read_project_log`) — fast dashboard repaint with no agent round-trip.
- **Hosted mode**: same file lives inside the Foundry microVM — client **cannot** read it. Only path is to POST a prompt and have the skill (running in the microVM) read its own `project_log.json` and emit a `dashboard_payload`.

If `desktop-client/app.py` references `project_log.json`, it's local-only. If `agent/skills/sow-response/SKILL.md` references it, it's the canonical producer/reader. Both are intentional. Do not "unify" them — they read different filesystems.

### 9.4 Multi-project parallelism

- Each project's `agent_session_id` resolves to its own `$HOME` subtree (local) or its own microVM (hosted). Concurrent posts to different sessions don't interfere.
- Hosted sessions can be idle-reaped silently; the only signal is the next post returning `first_run` (skill sees no `project_log.json`). A "session expired, re-kickoff?" UX path is not yet implemented.
- The desktop client foregrounds one project at a time. For true client-side parallelism, open multiple instances.
- **Never copy `session_id` across projects.** Each project mints/stores its own; reusing one ID for two projects merges their state.

### 9.5 Client-side state file (`~/.charter-agent/projects.json`)

Pointer index keyed by mode → project_id → `{label, customer_name, session_id, previous_response_id, is_new, created_at, last_used_at}`. **Pointer only — not state.** Losing a `session_id` here doesn't lose the project's data (still in the microVM), but it does orphan the pointer and force re-kickoff unless the old id can be recovered from logs.

**Don't write this file from PowerShell 5 with `Set-Content -Encoding utf8`** — it prepends a UTF-8 BOM, the client's `_load_projects` raises on `json.loads`, the except clause returns an empty store, and the user's entire project list appears wiped. Use `[System.IO.File]::WriteAllText($p, $json, (New-Object System.Text.UTF8Encoding $false))`. Verify first 3 bytes are `{`/`\r`/`\n` (`123, 13, 10`), never `EF BB BF`.

### 9.6 Runtime state files must stay out of git

- `.gitignore` only suppresses **untracked** paths. Files committed before a rule was added remain tracked and show up modified on every save.
- Parent-directory ignore patterns (`**/.charter-agent-home/`) don't retroactively untrack children. Use `git rm -r --cached <dir>` once, then commit the deletion.
- Explicit per-file patterns belong alongside the parent dir rule: `**/project_log.json`, `**/project_charter.md`, `**/projects.json`, `**/activity.json`, `**/state.json`, `**/charter.json`, `**/agent_session/`. Catches the file even when a smoke writes it outside the canonical `$HOME`.

### 9.7 Keep the bridge debug logs

The `[bridge] POST … session=… prev_resp=…` / `[bridge] <- status=… ct=…` / `[bridge] evt#N type=…` / `[bridge] stream done: events=N response_id=… text_parts=N consent=…` lines in `desktop-client/app.py::_post_one` are how every one of the silent failure modes above gets diagnosed in one pass. Do not strip them when "cleaning up."

---

## 10. What this project deliberately is not

It is helpful to be explicit about scope so good ideas don't accidentally derail it.

- **Not a workflow engine.** No DAGs, no BPMN, no Step Functions. The Charter is data, not a workflow definition.
- **Not a Teams app (yet).** The client surface is the desktop client. A Teams-tab packaging is a possible future surface but not a current one.
- **Not a multi-tenant SaaS.** Single tenant deployment, multi-project within that tenant. Don't add tenant-isolation plumbing.
- **Not an enterprise BI tool.** The dashboard shows project state, not aggregated cross-project analytics.
- **Not a replacement for Project, Planner, or Azure DevOps.** It coordinates the human work around delivering an artifact; it does not replace task management for engineering teams.

---

## 11. Build contract reference

The substance is in [architecture/architecture_and_design.md](architecture/architecture_and_design.md). This section consolidates the contract any builder needs at a glance so they don't have to scroll a 700-line doc to wire a module. If anything here drifts from the architecture doc, the architecture doc wins — open a PR fixing one or the other in the same change.

### 11.1 Environment variables

| Name | Set by | Required | Purpose |
|---|---|---|---|
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | hosted-agent env (set via `scripts/deploy.py`) | **yes** | Host runtime's Foundry model deployment (`gpt-5.x`). Used by `runtime/foundry_host.py` via Managed Identity. Boot fails if absent. Dev pin: `gpt-5.4` (plain `gpt-5` is **not** a deployment on the `ocvp-agent-svc` project — list with `az cognitiveservices account deployment list -n ocvp-agent-svc-resource -g pcdotai-agent`). |
| `FOUNDRY_AGENT_SESSION_ID` | Platform (per-request) / client (mirrored to `project_id`) | yes | Host runtime's MAF `AgentSession` resume key. Maps 1:1 to Foundry session. |
| `FOUNDRY_PROJECT_ENDPOINT` | Platform (auto-injected) | yes | Base URL for Foundry; used both for the host model and as the base for the Toolbox URL. |
| `TOOLBOX_NAME` | hosted-agent env (set via `scripts/deploy.py`) | yes | Foundry Toolbox name. Defaults to `Charter-Agent-Tools`. `runtime/foundry_host.py` builds the `MCPStreamableHTTPTool` URL from `FOUNDRY_PROJECT_ENDPOINT` + this name (+ optional `TOOLBOX_VERSION`). |
| `TOOLBOX_VERSION` | `.env` (dev) / unset (prod) | no | Pin a Toolbox version for local iteration (e.g. `"1"`); leave unset in production to use the consumer endpoint. |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Platform (auto-injected) | yes | OTel exporter destination; `agent-framework-foundry-hosting` and MAF wire the exporter automatically. No manual setup. |

The client additionally sets these **per request** on outbound `/responses` calls (see §11.7).

### 11.2 `/responses` surface

The agent has no action-verb dispatcher. It serves the OpenAI-compatible Responses protocol on `/responses` and streams the standard SSE event sequence (`response.created` → `response.output_text.delta` → `response.completed`, plus tool-call events). Multi-turn continuity is the caller's responsibility: pass `previous_response_id` on every request after the first.

**Per-request skill dispatch.** `foundry_host.bootstrap()` builds one warm MAF `Agent` per loaded skill (keyed by skill name). `responses_host._ResilientResponsesHostServer` wraps `ResponsesHostServer` and overrides `_handle_inner_agent`:
1. Pre-routes the input items eagerly to set `state.active_project_id()` as a side effect.
2. Resolves the skill name: preamble `skill=` field → `project_log["skill"]` → default (`"sow-response"`).
3. Sets `self._agent` to the warm Agent for that skill before calling `super()._handle_inner_agent()`.

This is why the Foundry Responses protocol does not have per-session instruction routing at the platform level — it must be implemented in the container. The `self._agent` swap is the correct and only hook available without a second runtime.

All per-skill Agents share the same `FoundryChatClient` and `MCPStreamableHTTPTool`; only `instructions` and in-process `tools` differ. This respects invariant 12 (single runtime).

If a future need genuinely demands a typed verb envelope (e.g. a client wants `dashboard` payloads with strict shape), express it as a new MAF tool on the warm `Agent`, not as a parallel protocol surface.

### 11.3 Skills contract

All skills live in `agent/skills/{name}/SKILL.md`, are valid per the [agentskills.io spec](https://agentskills.io/specification) (see [§4.3](#43-agent-skills-format-agentskillsio-conformance) for the format contract and [§4.4](#44-core-code-vs-skill--the-decision-rule) for what belongs in a skill vs in code), loaded at process start by `runtime/skill_loader.py`, and injected as the warm `Agent`'s `instructions` (today: a single skill, `sow-response`). The skills set is generic across projects; per-project variety comes from the Charter the skills reason against. All skills run on the single host runtime — there is no codegen sub-agent or generated module path (see invariant 12).

**Current (shipped):**

| Skill | Responsibility | Modes | References |
|---|---|---|---|
| `sow-response` (v0.5) | End-to-end SOW response workflow: mode-detect (first-run vs resume), ground kickoff from a triggering email / meeting / prior artifact, propose+ratify Charter, fan out to M365 (Teams, email), poll channels for submissions, classify per [`references/CLASSIFICATION_RUBRIC.md`](agent/skills/sow-response/references/CLASSIFICATION_RUBRIC.md), update `project_log.json` status, draft (but never auto-send) nudges/clarifications. **Two-source rule**: RFP = requirements only; meeting notes = task owners only — customer contacts in the RFP must never become task assignees. | first-run (stamp → ground → charter → kickoff) and resume (poll → classify → digest → draft nudges) | [`OUTPUT_FORMAT.md`](agent/skills/sow-response/references/OUTPUT_FORMAT.md), [`SOW_SECTIONS.md`](agent/skills/sow-response/references/SOW_SECTIONS.md), [`COMMUNICATION_MATRIX.md`](agent/skills/sow-response/references/COMMUNICATION_MATRIX.md), [`CLASSIFICATION_RUBRIC.md`](agent/skills/sow-response/references/CLASSIFICATION_RUBRIC.md) |

**Aspirational (designed, not built):** the eight-skill split listed in §1.5. The split is appealing because each skill body would be shorter and individually testable, but the cut points are workflow-stage boundaries that the model crosses naturally inside one `sow-response` turn. Promote one of those slices out into its own skill only when (a) a second project workflow needs the same slice with different surrounding context, or (b) the `sow-response` body grows past ~500 lines / ~5 000 tokens (the agentskills.io progressive-disclosure budget).

Skill changes are code changes — reviewed, versioned, shipped with the agent image.

### 11.4 Schemas (summary)

**Current (shipped):** state shape is owned by the [`sow-response/SKILL.md`](agent/skills/sow-response/SKILL.md) body, not by Pydantic models. There is no `charter/schemas.py`. Files the skill writes/reads through the generic `state_*` tools, all under `$HOME`:

- **`project_charter.md`** — Markdown, not JSON. The ratified charter document the agent emits at kickoff and treats as immutable thereafter. Schema is whatever the skill's §3–§4 prompt produces.
- **`project_log.json`** — Per-project working state. Skill-defined shape; current keys include `project_id`, `customer`, top-level `status` (enum, may be `kicked_off` / `submitted` / `submitted_with_gaps` / `closed`), `kickoff_sent`, and `tasks[]` with per-task `task_id, title, owner_upn, status, kickoff_sent, last_polled_at, submissions[], runbook_requirements`. The skill body is authoritative — read [§9 of the skill](agent/skills/sow-response/SKILL.md) before changing the shape.
- **`activity.json`** — Append-only NDJSON written by `observability.log_activity(...)`. One object per line: `{at, actor, kind, summary}`. Used both as the human-facing audit narrative and as a recovery trail.
- **`agent_session/<session-id>.json`** — MAF `AgentSession` thread persistence (owned by MAF, not by skill code).
- **`state.json`** — Legacy Phase-1 counter for the `echo` verb. Don't add new fields here.

**Aspirational (designed, not built):** the Pydantic-modelled `charter.json` (versioned, immutable outside ratify/amend), `SuggestedAction` lifecycle, and `CandidateEvent` envelope described in earlier drafts of this contract and in [architecture §5](architecture/architecture_and_design.md). Promote a file to a Pydantic schema only when (a) more than one Python module reads or writes it (today only the skill body does), or (b) an external surface needs a typed contract on it.

### 11.5 Agent boot sequence

The exact ordered steps `__main__.py` must execute. Refuse to start if step 1 fails.

1. **Assert env policy** — `AZURE_AI_MODEL_DEPLOYMENT_NAME` present, `FOUNDRY_PROJECT_ENDPOINT` present, `TOOLBOX_NAME` present. Hard-fail on any miss.
2. **Enable tracing** — register `ProcessAttributesSpanProcessor` before any spans are created so every span gets `project.id` + `gen_ai.conversation.id`. The App Insights exporter is wired by the platform via the auto-injected `APPLICATIONINSIGHTS_CONNECTION_STRING` — do not call `configure_azure_monitor` again. Do **not** call `AIProjectInstrumentor().instrument()` while on `azure-ai-projects` 2.0.1/2.1.0 (its Responses instrumentor's `AsyncStreamWrapper` lacks the `.headers` attribute `agent_framework_foundry`'s streaming consumer reads, crashing every turn); leave `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING` unset. Server-side spans are emitted by the Foundry platform regardless.
3. **`foundry_host.bootstrap()`** — this single call does everything below in order; do not duplicate any of it in `__main__.py`:
   - Builds `FoundryChatClient(project_endpoint=..., model=<deployment>, credential=DefaultAzureCredential())` from `agent-framework-foundry`.
   - Builds `MCPStreamableHTTPTool(name=<toolbox_name>, url=<toolbox-url>, http_client=<auth-injecting AsyncClient>, load_prompts=False)`. The mandatory `Foundry-Features: Toolboxes=V1Preview` header and `Authorization` bearer must be stamped by the `http_client`'s `event_hooks={"request": [...]}` on every outbound request — `header_provider` only fires on `tools/call`, not on `initialize` / `tools/list`. Pass `load_prompts=False` (Toolbox does not implement `prompts/list`).
   - Registers `STATE_TOOLS` via `skill_loader.register_tools(list(STATE_TOOLS))`.
   - Calls `skill_loader.load_bundles()` — which internally calls `load_all()`, resolves in-process vs external tools per the `allowed-tools` frontmatter policy, and builds one `SkillBundle` per skill. **Do not call `skill_loader.load_all()` from `__main__.py` after `bootstrap()` — it clears the bundle cache that `bootstrap()` just built.**
   - Builds one warm `Agent(client=<FoundryChatClient>, instructions=<skill body>, tools=[<toolbox>, *<inprocess_tools>], default_options={"store": False})` per `SkillBundle`, stored in `foundry_host._agents`.
4. **Log loaded skills** via `skill_loader.loaded_names()` — reads the already-populated cache without triggering a reload.
5. **`responses_host.start(foundry_host.get_all_agents())`** — passes the full dict of warm per-skill Agents to `_build_resilient_host()`, which constructs `_ResilientResponsesHostServer` and calls `.run()`. The server emits the OpenAI Responses SSE event stream and root OTel spans automatically. There is no verb-dispatch orchestrator; per-request skill dispatch is done by `_ResilientResponsesHostServer._handle_inner_agent` (see §11.2).

### 11.6 Tool dispatch (native MAF `MCPTool`)

MAF's `MCPTool` is the production code path. It handles MCP `initialize`, the `mcp-session-id` header, `notifications/initialized`, `tools/list` caching, `tools/call` (streamed where supported), and approval-item items. There is **no** project-owned `McpBridge` class on the production path.

Wiring requirements (set once at boot inside `runtime/foundry_host.py`):

- `server_url` → `TOOLBOX_MCP_ENDPOINT`.
- `server_label` → `"workiq"` (stable across Toolbox version switches).
- `require_approval` → `"never"` (gating is enforced at a higher layer — the skill body refuses to call write tools without explicit user OK; not at the MCP layer).
- Headers → always include `Foundry-Features: Toolboxes=V1Preview` and the Toolbox-channel `Authorization: Bearer <managed-identity-token>` (scope `https://ai.azure.com/.default`). Both are stamped by the `httpx.AsyncClient`'s request `event_hooks` (not by `header_provider` alone — that callback fires only on `tools/call`).
- Per-user identity propagation into the MCP call is done by the Foundry platform via OAuth Identity Passthrough on the Toolbox connections (invariant 3). The agent does not source or forward user tokens.
- Fan out channel polls concurrently (`asyncio.gather`) in the capture loop — the 100-second non-streaming MCP-call timeout is per-call, not per-batch.

[architecture/samplecode_toolbox.py](architecture/samplecode_toolbox.py) is **reference-only**. Its hand-rolled `McpBridge` (initialize / `mcp-session-id` / streamed `tools/call` / name sanitisation `.`/`-` → `_`) is exactly what `MCPTool` replaces. Keep it as a wire-shape debugging aid; do not import it from production code.

### 11.7 Client contract

The desktop client is the only client surface this sample ships. The [`spike/desktop_to_foundry/`](spike/desktop_to_foundry/) scripts are the reference implementation. On every request to the deployed agent's gated `/responses` endpoint:

| Concern | Rule |
|---|---|
| Routing | One Foundry session per project. The client uses `project_id` as both `?agent_session_id` and the `x-agent-chat-isolation-key` header. |
| Header `x-agent-chat-isolation-key` | Always `= project_id` (one Foundry session per project). |
| Header `Authorization` | `Bearer {user_token}` acquired by MSAL public client (or `az login` for dev) with scope `https://ai.azure.com/.default`. The Foundry platform propagates that identity into WorkIQ calls via Identity Passthrough (invariant 3). |
| Body | `{ "input": <user_message>, "previous_response_id": <id-or-null>, "stream": true }`. The host server owns multi-turn history via `previous_response_id`. |
| SSE | Pass through the standard OpenAI Responses event stream (`response.created`, `response.output_text.delta`, tool-call events, `response.completed`). The `oauth_consent_request` event carries a Microsoft login URL the client must open in a browser; once consent completes, the client retries the same prompt with `previous_response_id` set to the in-flight response. |
| Cold-start UX | Show "warming up…" for 2–5s after 15-min idle on the first request of a session. |

### 11.8 Test matrix

**Current (shipped, 23 tests passing as of May 22, 2026):**

| Layer | What's tested |
|---|---|
| `state.py` | Atomic write (temp+rename), JSON round-trip, NDJSON append-only-ness, path containment, `$HOME` isolation |
| `runtime/state_tools.py` | Each `@tool` wrapper round-trips through `state.py`; rejects path escapes |
| `runtime/skill_loader.py` | Rejects invalid YAML frontmatter; `name` must equal parent dir; description length bounds |
| `workiq/__init__.py` | Server enumeration & expected tool count |
| `orchestrator.handle_invocation` | All three verbs (`echo`, `list_tools`, `run_skill`); error envelope shape |

Test infra: `pytest`, `pytest-asyncio` (in `agent/tests/`); `conftest.py` autouse `isolated_home` fixture (sets `HOME` to `tmp_path`).

**Aspirational (designed, gated on the corresponding module landing):**

| Layer | What to test | Gate |
|---|---|---|
| `workiq/` thin wrappers | Correct MCP call shape, response parsing (`respx` mocks) | when `workiq/*.py` wrappers land |
| `runtime/foundry_host.py` | Warm `Agent` reuse within Foundry session; Toolbox-channel auth hook stamps Managed Identity bearer + `Foundry-Features` header on every request | always (currently only smoke-tested via `dev_run.py`) |
| `capture/handlers/*` | Cursor correctness (no missed/duplicated events across two polls), author filtering | when capture/ extracted from skill |
| `capture/` classifier | Golden-file tests against ≥30 labelled fixture events | when classifier extracted from skill; `RUN_HOST_MODEL_TESTS=1` |
| `status/triangulate.py` | Spec §8.3 truth table — parameterised across all combos | when status/ exists |
| `actions/` | Double-execute is idempotent no-op; dismissed cannot be re-approved | when actions/ exists |
| `charter/` | Ratification rejects invalid Charters; amendment increments `version`; orphan-dependency detection | when charter/schemas.py lands |
| Boot smoke | Boot fails fast if any required env var is missing; reaches healthy state on a real Foundry sandbox; a real `/responses` call returns the calling user's actual M365 data via Identity Passthrough | when production-bound |
| E2E (Phase 4+) | Bundled sample scenario end-to-end against test M365 tenant + dev Foundry project | manual, phase-gated |

Future CI gates to add when the corresponding modules land: `ruff`, `pyright`, `import-linter` (enforcing `runtime/foundry_host.py` as the sole instantiator of `Agent` and `FoundryChatClient`), `respx`, `freezegun`.

### 11.9 Dependency manifest

Pin these in `agent/pyproject.toml`. Do not substitute without an ADR.

**Agent runtime:** `agent-framework-core`, `agent-framework-foundry`, `agent-framework-foundry-hosting` (transitively pulls `azure-ai-agentserver-responses`), `mcp`, `aiohttp` (transitive of `azure-ai-projects` async pipeline), `azure-ai-projects>=2.0.0` (owns client-side tracing via `AIProjectInstrumentor` + `@trace_function`), `azure-identity`, `httpx`, `python-dotenv` (local-dev `.env` loading in `__main__` + scripts; no-op in hosted containers where the platform supplies env), `pydantic` v2, `pyyaml` (SKILL.md frontmatter), `opentelemetry-api`, `opentelemetry-sdk`, `azure-core-tracing-opentelemetry`, `azure-monitor-opentelemetry`.

**Agent dev/CI:** `import-linter`, `ruff`, `pyright`, `pytest`, `pytest-asyncio`, `respx`, `freezegun`.

**Desktop client (spike, [`spike/desktop_to_foundry/`](spike/desktop_to_foundry/)):** `msal`, `httpx`. No `pyproject.toml`; install ad-hoc.

### 11.10 Phases

Per [spec §9](functional-specs/project_workspace_spec.md). Do not skip ahead; each phase produces a runnable artifact. Status as of May 20, 2026.

| Phase | Scope | Verbs newly exercised | Status |
|---|---|---|---|
| 1. Skeleton | `__main__`, `runtime/foundry_host` (warm-only), `runtime/skill_loader`, `orchestrator`, `state` (counter) | `echo` | ✅ done |
| 2. Charter & kickoff | `sow-response/SKILL.md` §2–§7 (first-run mode) drives kickoff via WorkIQ Mail/Files/Teams/Tasks through the Toolbox; no separate `charter/` / `kickoff/` modules | (still via `run_skill`) | ✅ done (skill-driven, not modular) |
| 3. Skills | `sow-response/` ships with `references/` for sections, communication matrix, classification rubric, consolidation rules | (skill-internal) | ✅ done (one skill, not eight) |
| 4. Capture loop | `sow-response/SKILL.md` §8–§9 (resume + classify-and-flip); no separate `capture/` module yet; resume smoke validated end-to-end via `scripts/smoke_resume.py` | (still via `run_skill`) | 🟡 partial — resume + status update work via the skill; classify-and-flip awaits a real reply landing in the inbox to fully exercise |
| 5. Dashboard + approvals | Client-side rendering of dashboard payload, exceptions panel, action approval lifecycle | `execute_suggested`, `dismiss_suggested`, `coordinator_chat` | ⬜ not started |
| 6. Consolidation + closure | Skill-driven consolidation; amendment + close paths | `amend_charter`, `close_project`, `override_capture` | ⬜ not started |
| 7. Hardening | Idempotency edge cases, Conditional Access recovery via the `oauth_consent_request` flow, audit-log review | — | ⬜ not started |

The per-domain Python modules (`charter/`, `kickoff/`, `capture/`, `status/`, `actions/`, `consolidation/`) named in the original phase plan have **not** been built and may never be — the §4.4 decision rule keeps each behaviour in the skill until it earns a Python home.

---

## 12. Quick links

- [Requirement spec](functional-specs/project_workspace_spec.md)
- [Architecture & design](architecture/architecture_and_design.md)
- [References](functional-specs/references.md)
- [Sample meeting-notes file](test-fixtures/sample-meeting-notes.md) — **test input only**, one of many possible project shapes; not a normative scenario
- [Dashboard UI mock](test-fixtures/dashboard-mock.html) — illustrative reference for spec §5.6, depicting how the dashboard would render *for that particular sample's data*. Not the implementation, not the design contract.
- [test-fixtures/README.md](test-fixtures/README.md) — banner explaining the non-normative status of everything in that folder
- [Toolbox reference sample](architecture/samplecode_toolbox.py) — **reference-only** wire-shape sample; native MAF `MCPStreamableHTTPTool` is the production path
- [agentskills.io specification](https://agentskills.io/specification) — the format every skill in `agent/skills/` must conform to (invariant 1)
- [agentskills.io client showcase](https://agentskills.io/clients) — clients (Claude Code, VS Code, Goose, Gemini CLI, Kiro, fast-agent, …) where a SKILL.md authored here can be loaded for isolated testing
- [skills-ref validator](https://github.com/agentskills/agentskills/tree/main/skills-ref) — used in CI to gate PRs that touch `agent/skills/`
