# AGENTS.md — charter-agent

> Read this before every change. It is the operating contract for any coding agent (GitHub Copilot, Claude, Cursor, etc.) working in this repository. Humans should also read it before opening a PR.

This file follows the [agents.md](https://agents.md) convention. Sections 1–10 are the contract (invariants, tech choices, conventions, change-safety checklist). Section 11 is a consolidated build-time reference (env vars, action verbs, skills, schemas, boot sequence, MCP/Toolbox shape, BFF contract, test matrix, dependencies, phases) so a builder doesn't have to scroll the 700-line architecture doc to wire a single module. The architecture doc remains authoritative — if anything in §11 drifts from it, fix one or the other in the same PR.

---

## 1. What this project is

An **agent-orchestrated project coordination workspace**. A senior coordinator (Chief of Staff, programme manager, deal lead, audit lead) describes a cross-functional deliverable in natural language. A Microsoft Foundry hosted agent decomposes it into a **Project Charter**, kicks off the workstreams across Microsoft 365 (SharePoint, Teams, email, Outlook tasks), watches for deliveries across heterogeneous channels, infers status, drafts nudges and reassignments for human approval, and consolidates the final artifact.

One shared dashboard URL per project. Every stakeholder sees live status. When the project closes, the workspace dissolves.

The **canonical scenarios** are listed in [functional-specs/project_workspace_spec.md §2.4](functional-specs/project_workspace_spec.md). A sample meeting-notes file is bundled under [`test-fixtures/`](test-fixtures/) purely as **one** test input for the first end-to-end run — the agent must handle arbitrary projects, and nothing in the spec, architecture, or code should treat that sample's project name, owners, sections, or shape as fixed. Treat it the way you would a unit-test fixture.

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
2. **Generic over specific (the corollary of invariant 1).** The agent's *code* is project-shape-agnostic. *All* per-project variety lives in (a) the **Project Charter** (`$HOME/charter.json` in the session sandbox), (b) the **Agent Skills** under `agent/skills/` (auto-loaded by the host runtime; shape the agent's reasoning declaratively per workflow), and (c) — only when deterministic running code is genuinely required — a **Copilot-generated `consolidator.py`** in `$HOME/code/`. Do not hard-code domain logic — board pack, audit, escalation, budget, etc. — anywhere in the agent itself. If a feature seems to need it, the answer is almost always "extend the Charter schema, add or refine a skill, or (last resort) regenerate `consolidator.py`."
3. **WorkIQ runs in the coordinator's OBO context.** Every WorkIQ call — read or write, capture-time or send-time — is made in the **coordinator's delegated context** (the ratifier of the Charter), never as the agent's managed identity and never as the visiting collaborator. Application-only auth is not supported by WorkIQ ([spec §10.1](functional-specs/project_workspace_spec.md)). The Charter nominates a **deputy** (UPN) used as a fallback when the coordinator's token is unavailable (PTO, revoked, Conditional Access block). Per-visitor OBO is explicitly **not** used — collaborators see the dashboard via SSO, but the agent's view of M365 is the coordinator's view. This is what makes invariants 4 and 5 below tractable.
4. **No background workers, no cron, no schedulers.** The agent only runs when a user hits `/invocations`. There is no autonomous wake-up loop. Anything that *feels* like it needs scheduling is wrong — re-think it as "runs on next visit."
5. **Human-in-the-loop for every outbound action.** The agent drafts; the coordinator approves; the agent sends in the **coordinator's OBO context** (per invariant 3, not as a bot). There is no `auto_approve` mode. Do not add one.
6. **State lives in `$HOME`, period.** No external database, queue, cache, or event bus. The Foundry per-session microVM `$HOME` (Charter, `state.json`, `activity.json`, Copilot-generated code) is the entire project store. The frontend is a renderer; it holds no state.
7. **Charter immutability outside the ratification flow.** Only the `kickoff` and `amend_charter` code paths may write `charter.json`. Both must run through coordinator ratification. Increment `version` on every amendment.
8. **Use Invocations, not Responses.** The Foundry protocol choice is Invocations (we manage history ourselves). Don't introduce Responses-protocol code.
9. **Channel-watchers are a registry.** New `watch_channel.kind` values must plug into the registry described in [architecture §6.2](architecture/architecture_and_design.md). Never `if channel.kind == "...":` switches scattered in agent code.
10. **Idempotency on every outbound side-effect.** Every suggested action has a UUID; `state.executed_action_ids` is the gate. A double-approve must not double-send.
11. **No ports exposed from the sandbox.** The frontend Container App is the only public surface. The agent serves only `/invocations`.
12. **Dual-runtime: MAF `Agent` (host) + Copilot SDK (codegen sub-agent).** The agent has two runtimes, separated by purpose:
    - **Host runtime — Microsoft Agent Framework `Agent` (from `agent_framework`, *not* `ChatAgent`) on a Foundry `gpt-5.x` deployment, authenticated by Managed Identity.** Owns `/invocations` (served by `azure-ai-agentserver-invocations`), the session lifecycle (1:1 to Foundry session via `FOUNDRY_AGENT_SESSION_ID`), the conversational memory (MAF `AgentSession` thread persisted in `$HOME`), skill loading, Toolbox MCP tool dispatch (raw `MCPStreamableHTTPTool`), and all everyday reasoning — charter proposal, classification, drafting nudges, status triangulation, coordinator chat, consolidation orchestration. This is the agent. (Note: `agent-framework-core` 1.4.x exposes the host-agent class as `agent_framework.Agent` — `ChatAgent` does **not** exist at the package top level; constructor is `Agent(client=<FoundryChatClient>, instructions=..., *, tools=[...])`.)
    - **Codegen sub-agent — `CopilotClient` from [`github-copilot-sdk`](https://pypi.org/project/github-copilot-sdk/), used in-process** (`from copilot import CopilotClient`; no CLI, no subprocess), wrapped as a MAF agent via `CopilotClient.AsAIAgent()` / `GitHubCopilotAgent`. Authenticated by a fine-grained GitHub PAT (*Copilot Requests → Read-only*) passed **via constructor**, not env var. Used **only** by the `consolidate` skill, **only** to generate `$HOME/code/consolidator.py`, because Claude Opus 4.7 is materially better at multi-file Python generation than `gpt-5.x`. Not the host runtime; not on the everyday reasoning path; never the surface for coordinator chat.

    **Why split.** Distinct credential surfaces (MI for host + Toolbox + Foundry model; PAT only for codegen), distinct rate-limit pools, distinct failure modes. Constructor-passed PAT defeats the silent-backend-flip hazard in the Copilot SDK (env-var `GITHUB_TOKEN` + `AZURE_AI_MODEL_DEPLOYMENT_NAME` both present → SDK quietly uses the Foundry backend; see [references.md §9](functional-specs/references.md)). The boot sequence asserts **both** `AZURE_AI_MODEL_DEPLOYMENT_NAME` and `GITHUB_TOKEN` are present (one for the host model, one for the codegen sub-agent).

    **What this forbids.** Do not introduce a third runtime. Do not call `CopilotClient` from anywhere except `runtime/copilot_codegen.py` (enforced by `import-linter`). Do not move everyday reasoning onto the codegen sub-agent for "quality" reasons — if the host model is insufficient for a step, surface a Foundry `gpt-5.x` deployment as a named MAF tool for that step. See [§4.2](#42-model-assignment-policy).

A longer, scenario-flavoured version of these invariants is in [spec §10 "Things easy to get wrong"](functional-specs/project_workspace_spec.md). Read it. **Invariant 1 (skills-first) is the one most likely to get quietly violated under time pressure** — every time you find yourself reaching for a `match` statement, an `if project_kind == "..."` branch, or a hard-coded prompt fragment inside `orchestrator.py` or any `*/handlers/*.py`, stop and ask whether it belongs in a skill instead.

---

## 4. Technology choices (locked)

| Layer | Choice | Notes |
|---|---|---|
| Host agent runtime | **Microsoft Agent Framework `Agent`** (`agent-framework-core` + `agent-framework-foundry`, both pinned to 1.4.x; top-level import is `agent_framework`; the host-agent class is `Agent`, not `ChatAgent`) on a Foundry `gpt-5.x` deployment via Managed Identity, served by [`azure-ai-agentserver-invocations`](https://pypi.org/project/azure-ai-agentserver-invocations/) | Owns `/invocations`, session lifecycle, MAF `AgentSession` memory, raw `MCPStreamableHTTPTool` dispatch against the Toolbox, skill loading, every reasoning verb. One warm `Agent` per process; one MAF thread per Foundry session, keyed by `FOUNDRY_AGENT_SESSION_ID`. The Invocations server emits OpenTelemetry traces automatically. The PyPI distribution `agent-framework-azure-ai` is broken (rc5/rc6 import a renamed symbol) — do not add it back. |
| Codegen sub-agent | **GitHub Copilot SDK** ([`github-copilot-sdk`](https://pypi.org/project/github-copilot-sdk/)) wrapped as a MAF agent via `CopilotClient.AsAIAgent()` / `GitHubCopilotAgent`, used **in-process** | Used **only** by the `consolidate` skill to generate `$HOME/code/consolidator.py`. Runs on GHCP's default model (Claude Opus 4.7 today). PAT passed **via constructor**, not env var — see [§4.2](#42-model-assignment-policy). Not on any everyday reasoning path. |
| Agent hosting | **Foundry hosted agents** (Invocations protocol) | **Stateful** — per-session microVM, `$HOME` persists across `/invocations` requests (15-min idle / 30-day session ceiling). |
| Host model | **Foundry `gpt-5.x` deployment** via Managed Identity (`DefaultAzureCredential`) | Every reasoning verb except `consolidator.py` generation. `AZURE_AI_MODEL_DEPLOYMENT_NAME` is the deployment name. In the dev Foundry project (`ocvp-agent-svc`) the available deployments are `gpt-5.2`, `gpt-5.2-codex`, `gpt-5.3-chat`, `gpt-5.4`, `gpt-4.1-mini`, `gpt-4o`, `sora-2`, `gpt-image-1.5` — **plain `gpt-5` is not deployed**, so the dev `.env` pins `gpt-5.4`. Verify with `az cognitiveservices account deployment list -n ocvp-agent-svc-resource -g pcdotai-agent` if changing. |
| Codegen model | **GHCP default model** (currently Claude Opus 4.7) via fine-grained GitHub PAT (*Copilot Requests → Read-only*) | Only invoked from `runtime/copilot_codegen.py`, only by the `consolidate` skill. |
| Per-project specialisation | **Agent Skills** in `agent/skills/*/SKILL.md` (loaded at process start by `runtime/skill_loader.py`) | Primary mechanism. Skills are declarative, transparent, auditable, agentskills.io-conformant. The skills set is generic across projects; per-project variety comes from the Charter the skills reason against. |
| Per-project running code (exceptional) | **Copilot-generated `consolidator.py`** in `$HOME/code/` | Generated at kickoff and on amendment when consolidation logic genuinely needs deterministic Python (template-specific Word stitching, cross-section numeric reconciliation). Renderer and compliance behaviour are skills, not modules. |
| M365 data plane | **WorkIQ MCP servers**, delegated auth in the coordinator's OBO context | Always coordinator OBO with deputy fallback ([invariant 3](#3-non-negotiable-architectural-invariants)). Application-only is not supported by WorkIQ. |
| Bundling multiple MCP servers | **Foundry Toolboxes** (MCP-compatible endpoint), consumed by the host runtime as a raw MAF `MCPStreamableHTTPTool` | All WorkIQ MCP servers are bundled in a Foundry Toolbox named `Charter-Agent-Tools`. The host runtime declares a single `MCPStreamableHTTPTool` against the Toolbox URL with a `header_provider` that stamps the bearer + `Foundry-Features: Toolboxes=V1Preview` header; MAF handles `initialize` / `tools/list` / `tools/call` plumbing. See [§4.1](#41-foundry-toolbox-via-native-maf-mcpstreamablehttptool), [references.md §8](functional-specs/references.md). The portal-generated `samplecode_toolbox.py` is reference-only — its hand-rolled `McpBridge` is what `MCPStreamableHTTPTool` replaces. |
| Frontend | **Python + FastAPI BFF + small SPA** in **Azure Container Apps**, MSAL/Entra SSO | One deployment per tenant; multi-project via `/p/{project_id}` routing. The BFF sets `x-ms-chat-isolation-key = project_id` on every `/invocations` call **regardless of which collaborator is calling**, so all collaborators on a project hit the same Foundry session, same `$HOME`, same MAF thread. WorkIQ calls run in the coordinator's OBO context (invariant 3) — the visitor's identity is used for dashboard auth/role filtering only, not for M365 calls. |
| Identity | **Entra ID** — Foundry-assigned Agent ID (Managed Identity) for host model + Toolbox; coordinator's delegated token (cached via OBO flow with refresh) for WorkIQ; frontend app reg with `User.Read` | Conditional Access applies; plan for it (deputy fallback per invariant 3). |
| Observability | **Foundry agent tracing** (preview for hosted agents) — server-side spans emitted automatically by Foundry once App Insights is connected; client-side spans via `azure-ai-projects` `AIProjectInstrumentor().instrument()` (opt-in env `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true`); custom spans via the `@trace_function` decorator. Plus `$HOME/activity.json`. | Both, always. No manual OTel exporter wiring, no hand-rolled span context managers. Process-wide attributes (`project.id`, `gen_ai.conversation.id`) are injected by one `SpanProcessor.on_start` at boot. |

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
- `approval_mode="never_require"` for WorkIQ reads **and** writes. Approval is enforced at a higher layer (the `actions/` module's `SuggestedAction` lifecycle, gated by the coordinator in the dashboard) — see [references.md §8](functional-specs/references.md).
- Inject the **coordinator's** delegated WorkIQ token (per invariant 3) inside the auth hook — `runtime/workiq_token.py` is the sole owner of token acquisition and refresh, with deputy fallback. Visitor identity is **not** propagated to the Toolbox. In dev before `workiq_token.py` lands, `DefaultAzureCredential` naturally resolves to the coordinator's `az login` identity, so calls go in the right context; this is **not** a production posture.
- Watch the 100-second non-streaming MCP call timeout in capture loops — fan out channel polls concurrently (`asyncio.gather`), not serially.

**Host model.** Wire the host `Agent` with `FoundryChatClient(project_endpoint=..., model=<deployment>, credential=DefaultAzureCredential())` from `agent-framework-foundry` (the `model` kwarg is the deployment name, e.g. `gpt-5.4` — there is no `model_deployment_name` kwarg), then construct `Agent(client=<that FoundryChatClient>, instructions=<skill body>, tools=[<MCPStreamableHTTPTool>])` from `agent_framework`. The class is `Agent`, not `ChatAgent` — the latter does not exist at the `agent_framework` top level in 1.4.x. Do **not** depend on `agent-framework-azure-ai` — it is broken on PyPI (its rc5/rc6 imports `BaseContextProvider`, renamed to `ContextProvider` in every released core; adding it back will reproduce the original install failure).

**Reference wire shape.** The portal-generated `architecture/samplecode_toolbox.py` (hand-rolled `McpBridge` with explicit `initialize` / `mcp-session-id` / `notifications/initialized` / streamed `tools/call` / tool-name sanitisation `.`/`-` → `_`) is exactly what `MCPStreamableHTTPTool` owns for us. Keep the sample file in the repo as a debugging aid, but no production code path imports it.

**Standing rule.** Microsoft Learn's "Connect agents to MCP servers" + "Create and use a Foundry Toolbox" are the authoritative sources ([references.md §8](functional-specs/references.md)). For anything beyond what's documented there — a new tool, an unfamiliar schema, an MCP protocol-version bump — introspect the **live Toolbox endpoint on the fly** rather than coding to a stale snapshot.

### 4.2 Model assignment policy

There are **two** model paths, separated by purpose:

| Path | Runtime | Model | Credential | Used for |
|---|---|---|---|---|
| **Host** (everyday) | MAF `Agent` (`agent_framework.Agent`) | Foundry `gpt-5.x` deployment (dev pin: `gpt-5.4`) | Foundry-assigned Managed Identity (`DefaultAzureCredential`) | Every reasoning verb: charter proposal, classification, status triangulation, draft-outbound, coordinator chat, consolidation *orchestration*. |
| **Codegen sub-agent** (exceptional) | Copilot SDK `CopilotClient` (wrapped via `CopilotClient.AsAIAgent()`) | GHCP default (Claude Opus 4.7) | Fine-grained GitHub PAT, *Copilot Requests → Read-only*, passed **via constructor** | Generating `$HOME/code/consolidator.py` from the `consolidate` skill. Nothing else. |

**Credentials at deploy time:**

- `AZURE_AI_MODEL_DEPLOYMENT_NAME` is the host-runtime model deployment (`gpt-5.x`; the dev `.env` pins `gpt-5.4` because plain `gpt-5` is not a deployment on the `ocvp-agent-svc` project). Required at boot.
- `GITHUB_TOKEN` is the codegen sub-agent's PAT — a deployment secret stored in Key Vault and injected into the hosted agent as an env var by `agent.yaml` / the Bicep template. It must be a **fine-grained GitHub PAT** with *Copilot Requests → Read-only* permission (token format `github_pat_…`, `gho_…`, or `ghu_…`; classic `ghp_…` tokens are not supported). It must belong to a **service account**, not an individual — PATs tied to a person break the day that person leaves the org. Required at boot.
- `FOUNDRY_PROJECT_ENDPOINT` is auto-injected by the platform; used both for the host model and for the Toolbox.

**Why pass the PAT via constructor, not just rely on the env var.** The Copilot SDK's backend selection rule is: *if `GITHUB_TOKEN` and `AZURE_AI_MODEL_DEPLOYMENT_NAME` are both present in the SDK's env, the Foundry backend silently wins* ([Foundry sample README](https://github.com/microsoft-foundry/foundry-samples/blob/main/samples/python/hosted-agents/bring-your-own/invocations/github-copilot/README.md)). In our dual-runtime world both env vars are deliberately present — the host runtime needs `AZURE_AI_MODEL_DEPLOYMENT_NAME`. To defeat the silent flip, `runtime/copilot_codegen.py` reads `GITHUB_TOKEN` from the OS env and passes it to `CopilotClient(github_token=…)` (or the equivalent constructor argument), so the SDK's backend selector sees an explicit token and routes to GHCP regardless of what else is in the env.

**Things this rule forbids:**

- Do **not** widen the codegen sub-agent's usage beyond `consolidator.py` generation. If a skill needs Claude Opus for a non-codegen reasoning step, get an ADR first.
- Do **not** spawn a third LLM path (a separate Anthropic API key, a different OpenAI account, a parallel reasoning helper) without an ADR and an update to invariant 12. If a skill needs a different model for a specific step, expose it as a **named MAF tool** that calls another Foundry deployment with the agent's Managed Identity — do not create another runtime.

**Where this is enforced in code:**

- `agent/src/charter_agent/runtime/foundry_host.py` is the **only** module allowed to instantiate the host `Agent`. Enforced via `import-linter` contract.
- `agent/src/charter_agent/runtime/copilot_codegen.py` is the **only** module allowed to instantiate `CopilotClient`. Enforced via `import-linter` contract.
- `agent/src/charter_agent/codegen/` (for `consolidator.py` generation) is the **only** caller of `runtime/copilot_codegen.py`. Enforced via `import-linter` contract.
- The agent boot sequence asserts **both** `AZURE_AI_MODEL_DEPLOYMENT_NAME` and `GITHUB_TOKEN` are present. Refuses to start otherwise.

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
- A skill is **never** project-specific. If a skill seems to need per-project text, the per-project text belongs in the Charter (`charter.json`) and the skill reads it from there.
- Skills are reviewed and shipped with the agent container image. There is no "hot-load a skill from $HOME" path; the runtime path for *exceptional* deterministic per-project code remains `$HOME/code/consolidator.py` (see invariant 1 sub-bullet c).

**Out of scope / explicit non-goals here:**

- Claude-Code-specific concepts (`plugins/`, `mcp.json`, slash commands) are **not** part of agentskills.io and **not** used. Tool discovery is via the Foundry Toolbox over MCP ([§4.1](#41-foundry-toolbox-and-the-copilot-sdk-mcpbridge)).
- No external skill marketplace integration in MVP. "Portability" here means "a contributor can validate a skill locally in Claude Code or VS Code before opening a PR," not "users install third-party skills into a running project."

### 4.4 Core code vs skill — the decision rule

The single most common design question on this project will be "does this new functionality go into a `.py` module or into a new/existing skill?" The rule:

| Concern | Put it in | Why |
|---|---|---|
| Deterministic plumbing — atomic file I/O, JSON serialisation, schema validation, HTTP/MCP transport, request dispatch, OBO header propagation | **Code** (`agent/src/charter_agent/…`) | Invariants must hold byte-for-byte; not negotiable by an LLM. |
| State-of-the-world bookkeeping — `state.json` reads/writes, NDJSON append to `activity.json`, `executed_action_ids` gating, channel cursors | **Code** (`state.py`, `actions/`, `capture/handlers/*`) | Idempotency and audit integrity (invariants 10, 6) require strict, testable code. |
| Boot-time policy — env-var assertions, warm `Agent` lifecycle, MAF `MCPStreamableHTTPTool` wiring, OTel wiring | **Code** (`runtime/foundry_host.py`, `runtime/copilot_codegen.py`, `__main__.py`) | One-shot startup contract; no language-model judgement involved. |
| Verb dispatch — mapping `/invocations` action verbs to skill invocations + downstream effects | **Code** (`orchestrator.py`) | Dispatch is structural; the *reasoning* each verb triggers belongs in the skill it calls. |
| Channel-handler poll mechanics — "since" cursors, dedup keys, author filters | **Code** (`capture/handlers/*`) | Cursor correctness is testable; getting it wrong loses or duplicates events. |
| Classification, drafting, judgement, summarisation, gap detection, prompt-generation, document consolidation orchestration, coordinator conversation | **Skill** (`agent/skills/*/SKILL.md`) | These benefit from natural-language instructions, reference docs, and iteration without code review cycles; they are what LLMs do well. |
| Per-workflow procedural knowledge ("how a kickoff is done," "what makes a board-pack section compliant," "how to phrase a nudge respectfully") | **Skill** (with supporting `references/*.md`) | Captured once, version-controlled, swappable per organisation by editing one file. |
| Cross-section reconciliation that genuinely needs deterministic numeric checks and Word/Excel stitching | **Skill** (`consolidate`) **+** generated `$HOME/code/consolidator.py` it invokes | The skill orchestrates; the generated module does the deterministic byte-pushing. This is the only sanctioned codegen path. |

**The acid test for any new feature:** if you can describe the behaviour fully in English to a colleague in under 200 words, it should be a skill. If you can't — because it has bit-exact invariants, performance constraints, or security gates — it's code. When in doubt, prefer the skill; you can always lift bits down into code later if they prove non-negotiable.

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
│   ├── skills/                        ← Agent Skills (agentskills.io spec); auto-loaded by Copilot SDK at process start
│   │   ├── project-kickoff/
│   │   │   ├── SKILL.md               ← required: YAML frontmatter + instructions
│   │   │   ├── references/            ← optional: detail docs loaded on demand
│   │   │   └── assets/                ← optional: templates / schemas
│   │   ├── status-refresh/SKILL.md
│   │   ├── capture-classify/
│   │   │   ├── SKILL.md
│   │   │   └── references/CLASSIFICATION_RUBRIC.md
│   │   ├── compliance-check/
│   │   │   ├── SKILL.md
│   │   │   └── references/RUNBOOK_REQUIREMENTS.md
│   │   ├── draft-outbound/SKILL.md
│   │   ├── consolidate/
│   │   │   ├── SKILL.md
│   │   │   ├── scripts/               ← optional: helpers the skill may invoke
│   │   │   └── references/CONSOLIDATION_RULES.md
│   │   ├── amend-charter/SKILL.md
│   │   └── render-dashboard/SKILL.md
│   ├── src/charter_agent/
│   │   ├── __main__.py                ← azure-ai-agentserver-invocations entry; boots host + codegen runtimes
│   │   ├── runtime/
│   │   │   ├── foundry_host.py        ← sole owner of MAF Agent + AgentSession; native MCPTool wiring
│   │   │   ├── copilot_codegen.py     ← sole owner of CopilotClient (constructor-PAT); only called by codegen/
│   │   │   ├── skill_loader.py        ← reads agent/skills/*/SKILL.md, injects into Agent
│   │   │   └── workiq_token.py        ← coordinator OBO acquisition + deputy fallback + refresh
│   │   ├── orchestrator.py            ← top-level invocation dispatcher (action verbs → Agent / skill calls)
│   │   ├── charter/                   ← schema, validation, ratification, amendment
│   │   ├── kickoff/                   ← fan-out actions (SharePoint, Teams, Outlook, email)
│   │   ├── capture/                   ← watch-channel registry + handlers (skill-driven classifier)
│   │   ├── status/                    ← triangulation logic (pure)
│   │   ├── actions/                   ← suggested-action drafter + executor (with idempotency)
│   │   ├── codegen/                   ← exceptional codegen (consolidator.py only) via runtime/copilot_codegen
│   │   ├── consolidation/             ← invokes generated consolidator.py, surfaces findings
│   │   ├── state.py                   ← $HOME read/write helpers (MAF AgentSession persistence too)
│   │   ├── workiq/                    ← thin wrappers around WorkIQ MCP tools (the MAF MCPTool side)
│   │   └── observability.py           ← re-exports `@trace_function`, owns `$HOME/activity.json` + process-attribute span processor
│   └── tests/
├── frontend/                          ← Container App (BFF + SPA)
│   ├── Dockerfile
│   ├── backend/                       ← FastAPI; MSAL; Foundry Invocations client
│   └── ui/                            ← React 18 + TypeScript + Vite SPA
├── infra/                             ← Bicep or azd templates
│   └── main.bicep
└── .github/
    └── workflows/                     ← CI: lint, type-check, test, build, deploy
```

Two folders that are explicitly *not* in the repo: a `database/` and a `worker/` directory. If you find yourself wanting either, see invariants 4 and 6.

---

## 6. Conventions

- **Language**: Python 3.12 for the agent and the BFF. Type-hinted, `ruff` + `pyright` clean. No untyped public functions.
- **Tracing**: use the `@trace_function` decorator re-exported from `observability` for custom spans. Do not write `tracer.start_as_current_span(...)` by hand or build wrapper context managers — Foundry's instrumentor and the protocol server own the tracer provider and the App Insights exporter.
- **Audit log**: every state-mutating step calls `observability.log_activity(...)` to append to `$HOME/activity.json`. This is product behaviour (the narrative the dashboard renders), not telemetry. Never call `print()` or bare `logging.info` for things that belong in the audit log.
- **WorkIQ access**: Only through `agent/src/charter_agent/workiq/`. No direct MCP calls from orchestrator/kickoff/capture code. This makes mocking in tests possible. Token acquisition (coordinator OBO + deputy fallback) is the sole responsibility of `runtime/workiq_token.py`; the `workiq/` wrappers ask for a token from there and never reach for credentials directly.
- **Host runtime**: Only through `agent/src/charter_agent/runtime/foundry_host.py`. One MAF `Agent` instance per agent process (kept warm). One MAF `AgentSession` thread per Foundry session, keyed by `FOUNDRY_AGENT_SESSION_ID`, persisted to `$HOME` — never recreated within the same Foundry session.
- **Codegen sub-agent**: Only through `agent/src/charter_agent/runtime/copilot_codegen.py`. The `CopilotClient` is constructed with the PAT passed **as a constructor argument** (not read by the SDK from env) to defeat the silent backend-flip ([§4.2](#42-model-assignment-policy)). Only callable from `agent/src/charter_agent/codegen/`. Enforced by `import-linter`.
- **Skills**: Project-workspace specialisation lives in `agent/skills/{name}/SKILL.md` and must be valid per the [agentskills.io spec](https://agentskills.io/specification) (see [§4.3](#43-agent-skills-format-agentskillsio-conformance)). Loaded at boot by `runtime/skill_loader.py` and injected into the host `Agent`. CI runs `skills-ref validate ./agent/skills/*` on every PR that touches the directory; PR is blocked on failure. Skill changes are code changes — reviewed, versioned, shipped with the agent image. Do not write per-project skills; the skills set is generic, the Charter is what the skills reason against. Before adding any new feature, run it through the decision rule in [§4.4](#44-core-code-vs-skill--the-decision-rule).
- **JSON schemas**: Charter, state, suggested-action, and candidate-event schemas are defined in `agent/src/charter_agent/charter/schemas.py` as Pydantic models. The schemas in [architecture/architecture_and_design.md §5](architecture/architecture_and_design.md) are authoritative; the Pydantic models must round-trip them.
- **Tests**: every WorkIQ wrapper has a unit test with a fixture-mocked MCP response. Every action executor has a test proving double-execution is a no-op. Every channel handler has a test for the "since" cursor.
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

- [ ] Does the change preserve invariants 1–12 in §3?
- [ ] **Skills-first check (invariant 1):** if the change adds reasoning, drafting, judgement, classification, or any procedural domain knowledge, has it been packaged as a skill (or as edits to an existing skill) rather than added to a `.py` module? Run it through the [§4.4](#44-core-code-vs-skill--the-decision-rule) decision rule.
- [ ] **agentskills.io conformance check:** if the change touches `agent/skills/`, does `skills-ref validate` pass? Are `name` and `description` populated, with the description including trigger keywords?
- [ ] If it adds a domain-specific behaviour, has it been moved into the Charter schema, an existing skill, or a new skill — *not* into orchestrator/handler/kickoff code?
- [ ] If it touches `$HOME`, does it route through `state.py` and emit an activity-log entry?
- [ ] If it calls WorkIQ, is the call going through `workiq/` and using a token obtained from `runtime/workiq_token.py` (coordinator OBO with deputy fallback, per invariant 3)?
- [ ] If it adds an outbound action, does it have a UUID and a check against `executed_action_ids`?
- [ ] If it adds a new `watch_channel.kind`, is it registered in the channel registry (not switched on inline)?- [ ] If it adds a new `/invocations` action verb, is the action contract documented in [architecture §7](architecture/architecture_and_design.md)?
- [ ] If it changed the Charter, state, action, or candidate-event schema, were the architecture doc and the Pydantic models updated together?
- [ ] Lint, type-check, and tests pass.

---

## 9. Operational notes worth surfacing

- **Shared session per project, by design.** The BFF sets `x-ms-chat-isolation-key = project_id` and `FOUNDRY_AGENT_SESSION_ID = project_id` on every `/invocations` call regardless of caller. All collaborators on a project share one Foundry session → one `$HOME` → one Copilot conversational memory. Concurrent requests to the same session serialise on one compute instance; for a status-dashboard workflow this is fine and arguably desired. Per-user identity still flows through each invocation for OBO calls to WorkIQ.
- **50-concurrent-session preview limit per sub/region** translates to **50 active projects** in our model (not 50 concurrent users). Track this as projects scale.
- **`FOUNDRY_` env var prefix is reserved** by the platform and may be silently overwritten. Name internal env vars without that prefix — e.g. `TOOLBOX_MCP_ENDPOINT`, not `FOUNDRY_TOOLBOX_ENDPOINT`.
- **Build for `linux/amd64`.** Foundry hosted agents reject other architectures. Use `azd deploy` (ACR remote build) or `docker build --platform=linux/amd64 …` on Apple Silicon.

---

## 10. What this project deliberately is not

It is helpful to be explicit about scope so good ideas don't accidentally derail it.

- **Not a workflow engine.** No DAGs, no BPMN, no Step Functions. The Charter is data, not a workflow definition.
- **Not a Teams app (yet).** The first surface is the Container App dashboard. A Teams-tab packaging is a possible future surface but not a current one.
- **Not a multi-tenant SaaS.** Single tenant deployment, multi-project within that tenant. Don't add tenant-isolation plumbing.
- **Not an enterprise BI tool.** The dashboard shows project state, not aggregated cross-project analytics.
- **Not a replacement for Project, Planner, or Azure DevOps.** It coordinates the human work around delivering an artifact; it does not replace task management for engineering teams.

---

## 11. Build contract reference

The substance is in [architecture/architecture_and_design.md](architecture/architecture_and_design.md). This section consolidates the contract any builder needs at a glance so they don't have to scroll a 700-line doc to wire a module. If anything here drifts from the architecture doc, the architecture doc wins — open a PR fixing one or the other in the same change.

### 11.1 Environment variables

| Name | Set by | Required | Purpose |
|---|---|---|---|
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | `agent.yaml` / Bicep | **yes** | Host runtime's Foundry model deployment (`gpt-5.x`). Used by `runtime/foundry_host.py` via Managed Identity. Boot fails if absent. Dev pin: `gpt-5.4` (plain `gpt-5` is **not** a deployment on the `ocvp-agent-svc` project — list with `az cognitiveservices account deployment list -n ocvp-agent-svc-resource -g pcdotai-agent`). |
| `GITHUB_TOKEN` | Bicep → Key Vault → `agent.yaml` env injection | **yes** | Codegen sub-agent PAT. Fine-grained, *Copilot Requests → Read-only*, service-account-owned. Token format `github_pat_…`, `gho_…`, or `ghu_…` (classic `ghp_…` unsupported). Read by `runtime/copilot_codegen.py` from the OS env and passed to `CopilotClient(…)` **as a constructor argument** so the SDK's backend selector cannot silently flip to the Foundry backend even though `AZURE_AI_MODEL_DEPLOYMENT_NAME` is also present. Boot fails if absent. |
| `FOUNDRY_AGENT_SESSION_ID` | Platform (per-request) / BFF (mirrored to `project_id`) | yes | Host runtime's MAF `AgentSession` resume key. Maps 1:1 to Foundry session. |
| `FOUNDRY_PROJECT_ENDPOINT` | Platform (auto-injected) | yes | Base URL for Foundry; used both for the host model and as the base for the Toolbox URL. |
| `TOOLBOX_NAME` | `agent.yaml` / Bicep | yes | Foundry Toolbox name. Defaults to `Charter-Agent-Tools`. `AzureAIProjectToolbox` derives the MCP URL from `FOUNDRY_PROJECT_ENDPOINT` + this name (+ optional `TOOLBOX_VERSION`). |
| `TOOLBOX_VERSION` | `.env` (dev) / unset (prod) | no | Pin a Toolbox version for local iteration (e.g. `"1"`); leave unset in production to use the consumer endpoint. |
| `COORDINATOR_OBO_TENANT_ID` / `COORDINATOR_OBO_CLIENT_ID` / `COORDINATOR_OBO_CLIENT_SECRET` | Bicep → Key Vault → `agent.yaml` | yes | Confidential-client credentials used by `runtime/workiq_token.py` to perform OBO on the coordinator's stored refresh token (and the deputy's, on fallback). |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Platform (auto-injected) | yes | OTel exporter destination; `azure-ai-agentserver-invocations` and MAF wire the exporter automatically. No manual setup. |

The BFF additionally sets these **per request** on outbound `/invocations` calls (see §11.7).

### 11.2 `/invocations` action verbs

All verbs share the response envelope `{ok: bool, action: str, result: {...}, dashboard?: {...}}`. `dashboard` is included on every state-mutating verb and on `render_dashboard`.

| Verb | Payload | `result` | Caller role |
|---|---|---|---|
| `propose_charter` | `{prompt: str}` | `{proposed_charter: Charter}` | coordinator |
| `ratify_charter` | `{charter: Charter}` (edits allowed) | `{}` | coordinator |
| `render_dashboard` | `{}` | `{}` | any (SSO + visibility filter) |
| `execute_suggested` | `{approve_action_id: str}` | `{}` | coordinator |
| `dismiss_suggested` | `{approve_action_id: str, reason: str}` | `{}` | coordinator |
| `amend_charter` | `{amendment: AmendmentSpec}` | `{}` | coordinator |
| `override_capture` | `{task_id: str, submission_id: str, action: "unmark"}` | `{}` | coordinator |
| `coordinator_chat` | `{message: str}` | `{...}` (may stream via SSE; may internally trigger other verbs) | coordinator |
| `close_project` | `{}` | `{}` | coordinator |

New verbs must be added to the orchestrator dispatcher *and* documented in [architecture §7](architecture/architecture_and_design.md) in the same PR.

### 11.3 Skills contract

All skills live in `agent/skills/{name}/SKILL.md`, are valid per the [agentskills.io spec](https://agentskills.io/specification) (see [§4.3](#43-agent-skills-format-agentskillsio-conformance) for the format contract and [§4.4](#44-core-code-vs-skill--the-decision-rule) for what belongs in a skill vs in code), loaded at process start by `runtime/skill_loader.py` and injected into the host `Agent`, and invoked via `foundry_host.run_skill(name, **inputs)`. The skills set is generic across projects; per-project variety comes from the Charter the skills reason against. The `consolidate` skill is the only one that crosses runtimes — its body instructs the host to delegate `consolidator.py` generation to the codegen sub-agent via a tool exposed by `runtime/copilot_codegen.py`.

| Skill | Responsibility | Inputs | Outputs | Invoked by |
|---|---|---|---|---|
| `project-kickoff` | Ground the kickoff prompt by calling `workiq.ask` for open-ended discovery, then drilling into specific files / messages / meetings / tasks with typed WorkIQ tools; the grounding source is unconstrained (triggering email or meeting, prior similar artifact, organisation runbook, or any combination). If the prompt cites a specific source, retrieve it directly; if it cites none, surface discovered candidates and cite which were used (flag plausible alternatives so the coordinator can redirect at ratification). Then fan out post-ratification: SharePoint folder, templated task files, briefing emails, Outlook tasks, Teams kickoff message | kickoff prompt, ratified Charter, stakeholders | execution confirmations, audit entries (including cited grounding sources) | `orchestrator.propose_charter` → skill (grounding + draft); `orchestrator.ratify_charter` → `kickoff.fanout` (execution) |
| `status-refresh` | Triangulate per-task status from submissions, channel signals, OOO calendar | state.tasks, channel signals, calendar | status ∈ `{Assigned, InProgress, Submitted, SubmittedWithGaps, Overdue}` | `orchestrator.render_dashboard` → `status.triangulate` |
| `capture-classify` | Label a `CandidateEvent` as `submission ǀ revised_submission ǀ question ǀ supporting_material ǀ unrelated` | `CandidateEvent`, matching `Task`, Charter slice | `{label, confidence, rationale, needs_review}` (needs_review when confidence < 0.7) | capture loop |
| `compliance-check` | Validate submission against `task.runbook_requirements` | extracted submission content, requirements | per-requirement `{requirement_id, status: met ǀ unmet ǀ gap, evidence}` | capture loop (on `submission`) |
| `draft-outbound` | Draft nudges, clarifications, reassignments for coordinator approval | Charter, state, triangulated status | `SuggestedAction[]` | `orchestrator.render_dashboard` → `actions.draft` |
| `consolidate` | Orchestrate final-artifact assembly + cross-section reconciliation. Invokes generated `$HOME/code/consolidator.py` if present; otherwise reasons declaratively | Charter, state (all submissions), `consolidation_rules` | findings (matches, gaps, formatting issues), `output_path` | `orchestrator.render_dashboard` → `consolidation.run` |
| `amend-charter` | Walk coordinator through a Charter amendment (re-ratification flow per architecture §4.4); on accept, increment `version`, trigger consolidator regeneration if `consolidation_rules` changed | current Charter, proposed `AmendmentSpec` | new Charter (immutable until ratified) | `orchestrator.amend_charter` |

A `render-dashboard` skill also exists to serialise the SPA payload with viewer-role filtering (coordinator sees all, owner sees their tasks, observer sees summary). Skill changes are code changes — reviewed, versioned, shipped with the agent image.

### 11.4 Schemas (summary)

Authoritative Pydantic models live in `agent/src/charter_agent/charter/schemas.py`. Full JSON shape is in [architecture §5](architecture/architecture_and_design.md). What every builder needs to know:

**`charter.json` (v1, immutable outside ratify/amend):**
`project_id, version, project_kind, stakeholders {coordinator, owners, observers}, tasks[…], watch_channels[{kind, config}], consolidation_rules, deliverable {output_location, format}, consolidator_module_path?, ratified_at, ratified_by`.
Retired in v1 (do not reintroduce): `compliance_module_path`, `renderer_module_path`. Those behaviours are skills.

**`state.json` (per-Foundry-session in `$HOME`):**
`schema_version, project_id, session_started_at, last_full_refresh_at, last_full_refresh_by, tasks{task_id → {status, last_check, submissions[…], channel_signals}}, exceptions[…], suggested_actions{action_id → …}, executed_action_ids[], consolidation {last_run_at, output_path, findings[…]}, closed`.

**`activity.json` (append-only NDJSON, one object per line):**
`{at, actor, kind: kickoff ǀ capture ǀ classify ǀ draft_action ǀ execute_action ǀ amend ǀ consolidate ǀ close, ref, summary, span_id}`.

**`SuggestedAction`:**
`{action_id: UUIDv4, drafted_at, kind: nudge_owner ǀ clarify_gap ǀ propose_reassign ǀ propose_amendment, target {upn, channel}, reason, draft_payload, status: pending ǀ approved ǀ executed ǀ dismissed}`. Status is immutable once `executed` or `dismissed`. `execute_suggested` checks `action_id in state.executed_action_ids` **before** the WorkIQ side-effect call.

**`CandidateEvent`:**
`{event_id (channel-specific dedup key), channel, occurred_at, actor, summary, payload_ref}`.

### 11.5 Agent boot sequence

The exact ordered steps `__main__.py` must execute. Refuse to start if step 1 fails.

1. **Assert env policy** — `AZURE_AI_MODEL_DEPLOYMENT_NAME` present, `GITHUB_TOKEN` present, `FOUNDRY_PROJECT_ENDPOINT` present, `TOOLBOX_NAME` present, coordinator OBO confidential-client creds present. Hard-fail on any miss.
2. **Construct the host `Agent`** via `runtime/foundry_host.bootstrap()`. First build a `FoundryChatClient(project_endpoint=..., model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"], credential=DefaultAzureCredential())`, then `Agent(client=<chat_client>, instructions=..., tools=[<toolbox>])` (class is `agent_framework.Agent` — there is no `ChatAgent` in 1.4.x). Authenticate to the Foundry model deployment with `DefaultAzureCredential` (Foundry-assigned Managed Identity). Configure MAF `AgentSession` persistence to `$HOME/agent_session/`. Keep one `Agent` warm for process lifetime.
3. **Construct the Foundry Toolbox** via `MCPStreamableHTTPTool(name="workiq", url=<toolbox-url>, header_provider=<callable>, http_client=<auth-injecting AsyncClient>, approval_mode="never_require", request_timeout=90, load_prompts=False)` inside `runtime/foundry_host.bootstrap()`, and attach it as a tool on the warm host `Agent`. The mandatory `Foundry-Features: Toolboxes=V1Preview` header and the `Authorization` bearer **must** be stamped by the `http_client`'s `event_hooks={"request": [...]}` hook on every outbound request, because MAF's `header_provider` only fires on `call_tool` — not on `connect()` / `initialize` / `tools/list`. Pass `load_prompts=False` because the Foundry Toolbox does not implement `prompts/list` (otherwise `connect()` raises HTTP 400). The per-call WorkIQ token injector (coordinator OBO with deputy fallback) is registered when `runtime/workiq_token.py` lands; until then, calls run in whatever identity `DefaultAzureCredential` resolves to.
4. **Construct the codegen sub-agent** via `runtime/copilot_codegen.bootstrap()`. Read `GITHUB_TOKEN` from the OS env and pass it to `CopilotClient(github_token=…)` as a constructor argument (not via env propagation). Wrap as a MAF agent with `CopilotClient.AsAIAgent()` / `GitHubCopilotAgent`. Keep warm for process lifetime; only the `codegen/` module is allowed to invoke it.
5. **Load skills** via `runtime/skill_loader.load_all("agent/skills/")` — parse each `SKILL.md` YAML frontmatter (`name`, `description`, optional `metadata`, `allowed-tools`), validate against the agentskills.io shape, and register each skill body with the host `Agent` so the host model can select among them.
6. **Enable Foundry tracing** — set `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true`, call `AIProjectInstrumentor().instrument()` (from `azure.ai.projects.telemetry`), and register `ProcessAttributesSpanProcessor` on the global tracer provider. The App Insights exporter is wired by the platform via the auto-injected connection string — do not call `configure_azure_monitor` again.
7. **Start the Invocations server** via `azure-ai-agentserver-invocations`. Wire `/invocations` to `orchestrator.handle_invocation(action, payload, visitor_identity)`. The server emits root OTel spans automatically; the Foundry instrumentor and `@trace_function`-decorated functions produce children.

### 11.6 Tool dispatch (native MAF `MCPTool`)

MAF's `MCPTool` is the production code path. It handles MCP `initialize`, the `mcp-session-id` header, `notifications/initialized`, `tools/list` caching, `tools/call` (streamed where supported), and approval-item items. There is **no** project-owned `McpBridge` class on the production path.

Wiring requirements (set once at boot inside `runtime/foundry_host.py`):

- `server_url` → `TOOLBOX_MCP_ENDPOINT`.
- `server_label` → `"workiq"` (stable across Toolbox version switches).
- `require_approval` → `"never"` (gating is done in `actions/`, not at the MCP layer).
- Headers → always include `Foundry-Features: Toolboxes=V1Preview`. The `Authorization` header is **per-call**: a header-injector callback obtains the coordinator's WorkIQ delegated token from `runtime/workiq_token.py` (deputy fallback on failure) and stamps it on each outbound MCP request. Visitor identity is **not** propagated.
- Toolbox auth (for the MAF → Toolbox channel itself) → `DefaultAzureCredential` against `https://ai.azure.com/.default`.
- Fan out channel polls concurrently (`asyncio.gather`) in the capture loop — the 100-second non-streaming MCP-call timeout is per-call, not per-batch.

[architecture/samplecode_toolbox.py](architecture/samplecode_toolbox.py) is **reference-only**. Its hand-rolled `McpBridge` (initialize / `mcp-session-id` / streamed `tools/call` / name sanitisation `.`/`-` → `_`) is exactly what `MCPTool` replaces. Keep it as a wire-shape debugging aid; do not import it from production code.

### 11.7 Frontend BFF contract

The FastAPI BFF at `frontend/backend/` is the **only** caller of the agent's `/invocations`. On every request:

| Concern | Rule |
|---|---|
| Routing | URL `/p/{project_id}` → BFF extracts `project_id` and uses it as both the session key and the isolation key. |
| Header `x-ms-chat-isolation-key` | Always `= project_id` (overrides any per-user scoping; collaborators share one Foundry session and one `$HOME`). |
| Header `Authorization` | `Bearer {visitor_token}` for the visiting user (MSAL flow). The agent uses this **only** for dashboard auth and role filtering. **Not** propagated to WorkIQ — WorkIQ always sees the coordinator's OBO token (invariant 3). |
| Env `FOUNDRY_AGENT_SESSION_ID` | Set in the per-request Foundry client call to `project_id`. |
| Auth | `msal` (BFF flow), signed-cookie session via `itsdangerous`. Interactive fallback if Conditional Access blocks; surface CA blocks as a dedicated exception kind in state. |
| Dashboard response | Every state-mutating verb returns `dashboard` in the envelope; the SPA refreshes from that payload (no separate GET). |
| SSE | `coordinator_chat` streams MAF `Agent` response chunks as SSE `data:` frames terminated by `event: done`. Wire SSE pass-through from BFF to SPA. `render_dashboard` does **not** stream. |
| Cold-start UX | Show "warming up…" spinner for 2–5s after 15-min idle on first verb. |
| Per-role filter | Coordinator sees all; owner sees only their tasks; observer sees summary only. Enforced server-side by the `render-dashboard` skill. |

### 11.8 Test matrix

| Layer | What to test | Gate |
|---|---|---|
| `state.py` | Atomic write (temp+rename), Pydantic ↔ JSON round-trip, NDJSON append-only-ness, MAF `AgentSession` persistence round-trip | always |
| `workiq/` wrappers | Correct MCP call shape, token sourced from `runtime/workiq_token.py`, response parsing (`respx` mocks) | always |
| `runtime/workiq_token.py` | Coordinator OBO refresh; deputy fallback on coordinator-token failure; correct claims (`upn`, `oid`) on the issued token | always |
| `runtime/foundry_host.py` | Warm `Agent` reuse within Foundry session; `MCPTool` header injector receives a fresh coordinator token per call | always |
| `runtime/copilot_codegen.py` | PAT is passed via constructor (not env); only `codegen/` is allowed to import (import-linter); host model is **not** instantiated through this path | always |
| `runtime/skill_loader.py` | Rejects invalid YAML frontmatter; `name` must equal parent dir; description length bounds | always |
| `capture/handlers/*` | Cursor correctness (no missed/duplicated events across two polls), author filtering | always |
| `capture/` classifier | Golden-file tests against ≥30 labelled fixture events from the board-pack scenario | `RUN_HOST_MODEL_TESTS=1` |
| `status/triangulate.py` | Spec §8.3 truth table — parameterised across all combos | always |
| `actions/` | Double-execute is idempotent no-op; dismissed cannot be re-approved; outbound side-effects always sourced from coordinator OBO | always |
| `codegen/` | Generated `consolidator.py` passes smoke-fixture signature check; failed generation retries exactly once then raises; codegen sub-agent really reaches GHCP (not the Foundry backend) | `RUN_CODEGEN_TESTS=1` |
| `charter/` | Ratification rejects invalid Charters; amendment increments `version`; orphan-dependency detection; deputy UPN required and resolvable | always |
| Boot smoke | Boot fails fast if any required env var is missing; both runtimes reach a healthy state on a real Foundry sandbox; coordinator OBO yields a valid WorkIQ token | Phase 1.5 gate |
| E2E (Phase 4+) | Bundled sample meeting-notes scenario end-to-end against test M365 tenant + dev Foundry project | manual, phase-gated |

Test infra: `pytest`, `pytest-asyncio`, `respx`, `freezegun`, `import-linter` (CI gate enforcing `runtime/foundry_host.py` as the sole instantiator of `Agent` (and `FoundryChatClient`), `runtime/copilot_codegen.py` as the sole instantiator of `CopilotClient`, and `codegen/` as the sole caller of `runtime/copilot_codegen.py`).

### 11.9 Dependency manifest

Pin these in `agent/pyproject.toml`. Do not substitute without an ADR.

**Agent runtime:** `azure-ai-agentserver-invocations`, `agent-framework-core`, `agent-framework-foundry`, `mcp`, `github-copilot-sdk`, `azure-ai-projects>=2.0.0` (owns client-side tracing via `AIProjectInstrumentor` + `@trace_function`; future portal calls), `azure-identity`, `msal` (coordinator OBO refresh in `runtime/workiq_token.py`), `httpx`, `pydantic` v2, `python-docx`, `openpyxl` (pinned at agent level so generated `consolidator.py` cannot invent deps), `opentelemetry-api`, `opentelemetry-sdk`, `azure-core-tracing-opentelemetry`, `azure-monitor-opentelemetry`.

**Agent dev/CI:** `import-linter`, `ruff`, `pyright`, `pytest`, `pytest-asyncio`, `respx`, `freezegun`.

**Frontend BFF (`frontend/backend/pyproject.toml`):** `fastapi`, `uvicorn`, `msal`, `itsdangerous`, `httpx`.

**Frontend SPA (`frontend/ui/package.json`):** `react` 18, `react-dom` 18, `typescript` 5, `vite` 5, `react-router-dom` 6, `eslint`, `prettier`. CSS Modules. No TanStack Query / SWR / Tailwind / CSS-in-JS in MVP. Native `fetch` wrapped in a thin `bff.ts`. MVP component count: ~10; revisit when it exceeds ~30.

### 11.10 Phases

Per [spec §9](functional-specs/project_workspace_spec.md). Do not skip ahead; each phase produces a runnable artifact.

| Phase | Scope | Verbs newly exercised | Demoable outcome |
|---|---|---|---|
| 1. Skeleton | `__main__`, `runtime/foundry_host` (warm-only), `runtime/skill_loader` (load empty set), `orchestrator`, `state` (counter only), frontend skeleton | `echo` | Two browsers, same `project_id`, same counter; MAF `AgentSession` resumed across requests |
| **1.5. Dual-runtime smoke** — **gate** | `runtime/copilot_codegen` warmup; trivial `consolidate` skill that asks the codegen sub-agent for a one-line Python file; coordinator OBO `runtime/workiq_token` exercised end-to-end against test tenant | (none new — internal smoke only) | Host runtime answers a chat turn on Foundry model; codegen sub-agent reaches GHCP (verified via OTel attribute / token-issuer header) and writes `$HOME/code/smoke.py`; coordinator OBO yields a usable WorkIQ token | 
| 2. Charter & kickoff | `charter/`, `kickoff/`, `workiq/` (Mail/Files/Teams/Tasks), `project-kickoff` skill | `propose_charter`, `ratify_charter` | Real M365 fan-out for the bundled sample scenario |
| 3. Skills + exceptional codegen | All `agent/skills/*`, `codegen/` (consolidator only), `consolidation/` | (exercised by Phase 2/4 paths) | Skills loaded by `runtime/skill_loader`; generated `consolidator.py` visible in sandbox |
| 4. Capture loop | `capture/`, `status/triangulate`, `actions/` (draft only) | `render_dashboard` | Live status changes as deliveries land on channels |
| 5. Dashboard + approvals | SPA, exceptions panel, `actions/execute`, `actions/dismiss`, coordinator chat | `execute_suggested`, `dismiss_suggested`, `coordinator_chat` | Approve real Teams nudge sent as coordinator OBO |
| 6. Consolidation + closure | `consolidation/`, `charter/amend`, close path, `amend-charter` skill | `amend_charter`, `close_project`, `override_capture` | Reconciliation fires; project closes; deliverable on SharePoint |
| 7. Hardening | All modules; idempotency edge cases, CA-block recovery (deputy fallback), audit-log review | — | Production-ready: tests, recovery, telemetry complete |

---

## 12. Quick links

- [Requirement spec](functional-specs/project_workspace_spec.md)
- [Architecture & design](architecture/architecture_and_design.md)
- [References](functional-specs/references.md)
- [Sample meeting-notes file](test-fixtures/sample-meeting-notes.md) — **test input only**, one of many possible project shapes; not a normative scenario
- [Dashboard UI mock](test-fixtures/dashboard-mock.html) — illustrative reference for spec §5.6, depicting how the dashboard would render *for that particular sample's data*. Not the implementation, not the design contract.
- [test-fixtures/README.md](test-fixtures/README.md) — banner explaining the non-normative status of everything in that folder
- [Toolbox + Copilot-SDK reference sample](architecture/samplecode_toolbox.py) — **reference-only** under the dual-runtime architecture; native MAF `MCPTool` is the production path
- [agentskills.io specification](https://agentskills.io/specification) — the format every skill in `agent/skills/` must conform to (invariant 1)
- [agentskills.io client showcase](https://agentskills.io/clients) — clients (Claude Code, VS Code, Goose, Gemini CLI, Kiro, fast-agent, …) where a SKILL.md authored here can be loaded for isolated testing
- [skills-ref validator](https://github.com/agentskills/agentskills/tree/main/skills-ref) — used in CI to gate PRs that touch `agent/skills/`
