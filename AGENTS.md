# AGENTS.md — charter-agent

> Read this before every change. It is the operating contract for any coding agent (GitHub Copilot, Claude, Cursor, etc.) working in this repository. Humans should also read it before opening a PR.

This file follows the [agents.md](https://agents.md) convention. Sections 1–10 are the contract (invariants, tech choices, conventions, change-safety checklist). Section 11 is a consolidated build-time reference (env vars, action verbs, skills, schemas, boot sequence, `McpBridge` shape, BFF contract, test matrix, dependencies, phases) so a builder doesn't have to scroll the 700-line architecture doc to wire a single module. The architecture doc remains authoritative — if anything in §11 drifts from it, fix one or the other in the same PR.

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

1. **Skills-first, conformant to the open [agentskills.io](https://agentskills.io/specification) spec.** This is the most load-bearing architectural choice in the system, above every other invariant on this list. Every reusable agent capability — classify, draft, validate, consolidate, propose, render — is packaged as an **Agent Skill**: a folder under `agent/skills/{name}/` containing a `SKILL.md` with valid YAML frontmatter (required `name` and `description`; optional `metadata`, `license`, `compatibility`, `allowed-tools`), plus optional `scripts/`, `references/`, `assets/` subdirs. Skills are progressively disclosed (name+description always loaded at boot, body on activation, supporting files on demand). The Copilot SDK's `skills/*/SKILL.md` auto-loader is a compliant agentskills.io runtime — GitHub Copilot is in the spec's [client showcase](https://agentskills.io/clients). Conformance buys us portability (the same skill can be loaded by Claude Code, VS Code, Goose, Gemini CLI, Kiro, fast-agent, etc. for isolated testing), auditability (skills are versioned files reviewed in PRs), and a clean cut between *deterministic plumbing* (code) and *reasoning/generation/judgement* (skill). See [§4.3](#43-agent-skills-format-agentskillsio-conformance) for the format contract and [§4.4](#44-core-code-vs-skill--the-decision-rule) for the heuristic. Note: agentskills.io is a *format* spec, not a runtime or tool-discovery mechanism — Claude-Code-specific concepts like `plugins/` and `mcp.json` are **not** part of it and **not** used here; tool discovery for us is the Foundry Toolbox over MCP (see [§4.1](#41-foundry-toolbox-and-the-copilot-sdk-mcpbridge)).
2. **Generic over specific (the corollary of invariant 1).** The agent's *code* is project-shape-agnostic. *All* per-project variety lives in (a) the **Project Charter** (`$HOME/charter.json` in the session sandbox), (b) the **Agent Skills** under `agent/skills/` (auto-loaded by the Copilot runtime; shape the agent's reasoning declaratively per workflow), and (c) — only when deterministic running code is genuinely required — a **Copilot-generated `consolidator.py`** in `$HOME/code/`. Do not hard-code domain logic — board pack, audit, escalation, budget, etc. — anywhere in the agent itself. If a feature seems to need it, the answer is almost always "extend the Charter schema, add or refine a skill, or (last resort) regenerate `consolidator.py`."
3. **WorkIQ is delegated-only.** Every WorkIQ call runs in the **visiting user's OBO context**, never as the agent's managed identity. Application-only auth is not supported. See [spec §10.1](functional-specs/project_workspace_spec.md). This forces invariants 4 and 5 below.
4. **No background workers, no cron, no schedulers.** The agent only runs when a user hits `/invocations`. There is no autonomous wake-up loop. Anything that *feels* like it needs scheduling is wrong — re-think it as "runs on next visit."
5. **Human-in-the-loop for every outbound action.** The agent drafts; the coordinator approves; the agent sends in the **coordinator's OBO context** (not as a bot). There is no `auto_approve` mode. Do not add one.
6. **State lives in `$HOME`, period.** No external database, queue, cache, or event bus. The Foundry per-session microVM `$HOME` (Charter, `state.json`, `activity.json`, Copilot-generated code) is the entire project store. The frontend is a renderer; it holds no state.
7. **Charter immutability outside the ratification flow.** Only the `kickoff` and `amend_charter` code paths may write `charter.json`. Both must run through coordinator ratification. Increment `version` on every amendment.
8. **Use Invocations, not Responses.** The Foundry protocol choice is Invocations (we manage history ourselves). Don't introduce Responses-protocol code.
9. **Channel-watchers are a registry.** New `watch_channel.kind` values must plug into the registry described in [architecture §6.2](architecture/architecture_and_design.md). Never `if channel.kind == "...":` switches scattered in agent code.
10. **Idempotency on every outbound side-effect.** Every suggested action has a UUID; `state.executed_action_ids` is the gate. A double-approve must not double-send.
11. **No ports exposed from the sandbox.** The frontend Container App is the only public surface. The agent serves only `/invocations`.
12. **Single agent runtime — GitHub Copilot SDK.** The agent is a thin shell around a `CopilotClient` from the [`github-copilot-sdk`](https://pypi.org/project/github-copilot-sdk/) PyPI package, used **in-process** (`from copilot import CopilotClient`; no CLI, no subprocess). The Copilot session and the Foundry hosted-agent session map **1:1** keyed by `FOUNDRY_AGENT_SESSION_ID`; intelligent compaction in the SDK is the agent's conversational memory. All reasoning — charter proposal, classification, drafting nudges, coordinator chat, consolidation orchestration, and the (exceptional) generation of `consolidator.py` — runs through that single client. Auth is via `GITHUB_TOKEN` (a fine-grained GitHub PAT with *Copilot Requests → Read-only*) so the SDK runs on GHCP's default model (Claude Opus 4.7 today). **Do not** set `AZURE_AI_MODEL_DEPLOYMENT_NAME` in the agent's process env — the Copilot SDK would silently flip to the Foundry-model backend ([sample README](https://github.com/microsoft-foundry/foundry-samples/blob/main/samples/python/hosted-agents/bring-your-own/invocations/github-copilot/README.md): *"If both are set → the Foundry model takes precedence"*). The `gpt-5.x` deployments in the Foundry project remain reachable as named tools if a skill ever needs them, but they are not the agent's runtime. See [§4.2](#42-model-assignment-policy).

A longer, scenario-flavoured version of these invariants is in [spec §10 "Things easy to get wrong"](functional-specs/project_workspace_spec.md). Read it. **Invariant 1 (skills-first) is the one most likely to get quietly violated under time pressure** — every time you find yourself reaching for a `match` statement, an `if project_kind == "..."` branch, or a hard-coded prompt fragment inside `orchestrator.py` or any `*/handlers/*.py`, stop and ask whether it belongs in a skill instead.

---

## 4. Technology choices (locked)

| Layer | Choice | Notes |
|---|---|---|
| Agent runtime | **GitHub Copilot SDK** ([`github-copilot-sdk`](https://pypi.org/project/github-copilot-sdk/)) on top of [`azure-ai-agentserver-invocations`](https://pypi.org/project/azure-ai-agentserver-invocations/) | The `CopilotClient` is the agent. One warm client per process; one Copilot session per Foundry session, resumed via `FOUNDRY_AGENT_SESSION_ID`. Invocations protocol server (`azure-ai-agentserver-invocations`) serves `POST /invocations` and emits OpenTelemetry traces automatically. **No separate harness, no Microsoft Agent Framework wrapper** — the canonical Microsoft sample at `microsoft-foundry/foundry-samples/.../github-copilot` is the reference. |
| Agent hosting | **Foundry hosted agents** (Invocations protocol) | **Stateful** — per-session microVM, `$HOME` persists across `/invocations` requests (15-min idle / 30-day session ceiling). |
| Model | **GHCP default model** (currently Claude Opus 4.7) via `GITHUB_TOKEN` (fine-grained PAT, *Copilot Requests → Read-only*) | Single model surface — reasoning, drafting, classification, and (exceptional) codegen all run on this. `AZURE_AI_MODEL_DEPLOYMENT_NAME` **must not be set**, or the SDK silently flips to a Foundry-model backend. The `gpt-5.x` Foundry deployments stay available for any skill that explicitly wants a different model as a named tool. |
| Per-project specialisation | **Copilot SDK skills** in `agent/skills/*/SKILL.md` (auto-loaded at process start) | Primary mechanism. Skills are declarative, transparent, auditable. The skills set is generic across projects; per-project variety comes from the Charter the skills reason against. |
| Per-project running code (exceptional) | **Copilot-generated `consolidator.py`** in `$HOME/code/` | Generated at kickoff and on amendment when consolidation logic genuinely needs deterministic Python (template-specific Word stitching, cross-section numeric reconciliation). Renderer and compliance behaviour are skills, not modules. |
| M365 data plane | **WorkIQ MCP servers**, OAuth Identity Passthrough via the Foundry project's MCP connections | Delegated only. |
| Bundling multiple MCP servers | **Foundry Toolboxes** (MCP-compatible endpoint), consumed by the Copilot SDK via an **`McpBridge`** | All WorkIQ MCP servers are bundled in a Foundry Toolbox named `Charter-Agent-Tools`. The agent opens one MCP session to the Toolbox at boot, calls `tools/list`, maps each tool into a Copilot tool definition (substituting `.` and `-` with `_` — Copilot SDK rejects them), and routes Copilot tool calls back through the bridge. See [§4.1](#41-foundry-toolbox-and-the-copilot-sdk-mcpbridge), [references.md §8](functional-specs/references.md), and the working sample at [architecture/samplecode_toolbox.py](architecture/samplecode_toolbox.py). |
| Frontend | **Python + FastAPI BFF + small SPA** in **Azure Container Apps**, MSAL/Entra SSO | One deployment per tenant; multi-project via `/p/{project_id}` routing. The BFF sets `x-ms-chat-isolation-key = project_id` on every `/invocations` call **regardless of which collaborator is calling**, so all collaborators on a project hit the same Foundry session, same `$HOME`, same Copilot conversational memory. Per-user identity still flows through each invocation for OBO calls to WorkIQ. |
| Identity | **Entra ID** — Foundry-assigned Agent ID; frontend app reg with `User.Read`; reuse internal SSO if available | Conditional Access applies; plan for it. |
| Observability | **App Insights via OpenTelemetry** (auto-injected by Foundry; emitted by `azure-ai-agentserver-invocations` automatically) + `$HOME/activity.json` | Both, always. The protocol library handles OTel — no manual exporter wiring. |

Do **not** substitute these without an architectural decision record committed alongside the change.

### 4.1 Foundry Toolbox and the Copilot SDK `McpBridge`

The Copilot SDK does not ship a built-in MCP client. The agent therefore bridges between Copilot's tool interface and the Foundry Toolbox's MCP endpoint with an `McpBridge` helper, modelled exactly on the portal-generated sample at [architecture/samplecode_toolbox.py](architecture/samplecode_toolbox.py).

What the sample pins down — treat each as a hard requirement, not a suggestion:

- Version-pinned Toolbox URL format in dev (`/toolboxes/Charter-Agent-Tools/versions/{ver}/mcp?api-version=v1`); consumer endpoint (`/toolboxes/Charter-Agent-Tools/mcp?api-version=v1`) in prod.
- `DefaultAzureCredential` against the `https://ai.azure.com/.default` scope.
- The mandatory `Foundry-Features: Toolboxes=V1Preview` HTTP header on every request.
- MCP `initialize` → capture `mcp-session-id` → `notifications/initialized` handshake.
- `tools/list` once at agent boot (cache per process); `tools/call` per tool invocation, **always streamed** (`stream=True`) — non-streaming `tools/call` is not supported by the Toolbox today.
- Tool-name sanitisation at the Copilot-SDK boundary only: `.` and `-` → `_`. Do not rewrite tool names when calling MCP directly.
- Skip `prompts/list` (Copilot SDK should be constructed with `load_prompts=False` equivalent); `send_ping()` against the Toolbox returns 500 and must not be called.
- Reference: Microsoft Learn's "Use Foundry Toolboxes from the Copilot SDK" — https://aka.ms/foundry-toolbox-copilotsdk.

**Standing rule.** The sample is the starting reference, not a frozen contract. For anything beyond what it covers — a new tool, a new MCP method, a changed schema, an auth or header change, an MCP protocol-version bump — introspect the **live Toolbox endpoint on the fly** (`tools/list`, server `capabilities`, the response of `initialize`) rather than coding to a stale snapshot.

### 4.2 Model assignment policy

There is **one** model path in this system. The Copilot SDK runs on its bundled default model — currently **Claude Opus 4.7**, the same model GHCP uses in VS Code — authenticated by `GITHUB_TOKEN`. Reasoning, drafting, classification, coordinator chat, and the (exceptional) generation of `consolidator.py` all flow through the same `CopilotClient` session.

**Credentials at deploy time:**

- `GITHUB_TOKEN` is a deployment secret stored in Key Vault and injected into the hosted agent as an env var by `agent.yaml` / the Bicep template. It must be a **fine-grained GitHub PAT** with *Copilot Requests → Read-only* permission (token format `github_pat_…`, `gho_…`, or `ghu_…`; classic `ghp_…` tokens are not supported). It must belong to a **service account**, not an individual — PATs tied to a person break the day that person leaves the org.
- The agent's Foundry-assigned Managed Identity is used to call the **Toolbox** (and potentially `gpt-5.x` deployments if a skill explicitly opts in as a named tool). `FOUNDRY_PROJECT_ENDPOINT` is auto-injected by the platform.

**Things this rule forbids:**

- Do **not** set `AZURE_AI_MODEL_DEPLOYMENT_NAME` in the agent's process env. It silently flips the Copilot SDK to a Foundry-model backend (per the [Foundry sample README](https://github.com/microsoft-foundry/foundry-samples/blob/main/samples/python/hosted-agents/bring-your-own/invocations/github-copilot/README.md): *"If both are set → the Foundry model takes precedence"*) and our model surface is silently swapped without any compile-time error. The agent boot sequence asserts this env var is absent and refuses to start otherwise.
- Do **not** spawn a second LLM call path (a separate Anthropic API key, a different OpenAI account, a parallel MAF reasoning helper) without an ADR and an update to invariant 12. If a skill needs a different model for a specific step, expose it as a **named Copilot tool** that calls a Foundry `gpt-5.x` deployment with the agent's Managed Identity — do not create a second runtime.

**Where this is enforced in code:**

- `agent/src/charter_agent/copilot_runtime.py` is the **only** module allowed to instantiate `CopilotClient`. Enforced via `import-linter` contract.
- `agent/src/charter_agent/codegen/` (for `consolidator.py` generation) calls into `copilot_runtime.py` to acquire a session — it does not construct its own client.
- The agent boot sequence asserts: `GITHUB_TOKEN` present, `AZURE_AI_MODEL_DEPLOYMENT_NAME` absent. Refuses to start if either check fails.

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
compatibility: Requires GitHub Copilot SDK; Foundry Toolbox MCP endpoint   # optional; only if there are real environment requirements
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
- The `description` field is the *only* mechanism by which the orchestrator and the Copilot SDK route work to a skill — invest time in it. Bad descriptions are the most common reason a skill is silently ignored at runtime.
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
| Boot-time policy — env-var assertions, warm `CopilotClient` lifecycle, MCP handshake, OTel wiring | **Code** (`copilot_runtime.py`, `__main__.py`) | One-shot startup contract; no language-model judgement involved. |
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
│   │   ├── __main__.py                ← azure-ai-agentserver-invocations entry; warms CopilotClient
│   │   ├── copilot_runtime.py         ← sole owner of CopilotClient; per-session resume; tool registry
│   │   ├── orchestrator.py            ← top-level invocation dispatcher (action verbs → Copilot session prompts)
│   │   ├── charter/                   ← schema, validation, ratification, amendment
│   │   ├── kickoff/                   ← fan-out actions (SharePoint, Teams, Outlook, email)
│   │   ├── capture/                   ← watch-channel registry + handlers (skill-driven classifier)
│   │   ├── status/                    ← triangulation logic (pure)
│   │   ├── actions/                   ← suggested-action drafter + executor (with idempotency)
│   │   ├── codegen/                   ← exceptional codegen (consolidator.py only) via copilot_runtime
│   │   ├── consolidation/             ← invokes generated consolidator.py, surfaces findings
│   │   ├── state.py                   ← $HOME read/write helpers
│   │   ├── workiq/                    ← McpBridge + thin wrappers around the WorkIQ MCP tools
│   │   └── observability.py           ← OTel spans + activity-log emission
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
- **Logging**: Always go through `observability.py` — it emits both an OTel span and an `activity.json` entry. Never call `print()` or bare `logging.info` in agent code.
- **WorkIQ access**: Only through `agent/src/charter_agent/workiq/`. No direct MCP calls from orchestrator/kickoff/capture code. This makes mocking in tests possible. The `McpBridge` that exposes WorkIQ tools to the Copilot session also lives in this module — see [architecture/samplecode_toolbox.py](architecture/samplecode_toolbox.py) for the reference implementation.
- **Copilot SDK runtime**: Only through `agent/src/charter_agent/copilot_runtime.py`. One `CopilotClient` instance per agent process (kept warm). One Copilot session per Foundry session, resumed via `FOUNDRY_AGENT_SESSION_ID` — never recreated within the same Foundry session. The (exceptional) `consolidator.py` codegen path in `codegen/` borrows a session from `copilot_runtime.py`; it does not construct its own client.
- **Skills**: Project-workspace specialisation lives in `agent/skills/{name}/SKILL.md` and must be valid per the [agentskills.io spec](https://agentskills.io/specification) (see [§4.3](#43-agent-skills-format-agentskillsio-conformance)). CI runs `skills-ref validate ./agent/skills/*` on every PR that touches the directory; PR is blocked on failure. Skill changes are code changes — reviewed, versioned, shipped with the agent image. Do not write per-project skills; the skills set is generic, the Charter is what the skills reason against. Before adding any new feature, run it through the decision rule in [§4.4](#44-core-code-vs-skill--the-decision-rule).
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
- [ ] If it calls WorkIQ, is the call going through `workiq/` and explicitly in the visiting user's OBO context?
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
| `GITHUB_TOKEN` | Bicep → Key Vault → `agent.yaml` env injection | **yes** | Authenticates Copilot SDK on GHCP default model. Fine-grained PAT, *Copilot Requests → Read-only*, service-account-owned. Token format `github_pat_…`, `gho_…`, or `ghu_…` (classic `ghp_…` unsupported). |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | — | **must be unset** | Boot-time assertion in `copilot_runtime.bootstrap()` refuses to start if present (would silently flip the SDK to the Foundry-model backend; see §4.2). |
| `FOUNDRY_AGENT_SESSION_ID` | Platform (per-request) / BFF (mirrored to `project_id`) | yes | Copilot session resume key. Maps 1:1 to Foundry session. |
| `FOUNDRY_PROJECT_ENDPOINT` | Platform (auto-injected) | yes | Base URL for Foundry; also base for the Toolbox URL. |
| `TOOLBOX_MCP_ENDPOINT` | `agent.yaml` / Bicep (or derived from `FOUNDRY_PROJECT_ENDPOINT`) | yes | Full Toolbox MCP URL. Dev: `…/toolboxes/Charter-Agent-Tools/versions/{ver}/mcp?api-version=v1`. Prod: `…/toolboxes/Charter-Agent-Tools/mcp?api-version=v1`. **Do not** prefix with `FOUNDRY_` — that prefix is reserved by the platform. |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Platform (auto-injected) | yes | OTel exporter destination; `azure-ai-agentserver-invocations` wires the exporter automatically. No manual setup. |

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

All skills live in `agent/skills/{name}/SKILL.md`, are valid per the [agentskills.io spec](https://agentskills.io/specification) (see [§4.3](#43-agent-skills-format-agentskillsio-conformance) for the format contract and [§4.4](#44-core-code-vs-skill--the-decision-rule) for what belongs in a skill vs in code), auto-loaded by the Copilot SDK at process start, and invoked via `copilot_runtime.run_skill(name, **inputs)`. The skills set is generic across projects; per-project variety comes from the Charter the skills reason against.

| Skill | Responsibility | Inputs | Outputs | Invoked by |
|---|---|---|---|---|
| `project-kickoff` | Fan out post-ratification: SharePoint folder, templated task files, briefing emails, Outlook tasks, Teams kickoff message | ratified Charter, stakeholders | execution confirmations, audit entries | `orchestrator.ratify_charter` → `kickoff.fanout` |
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

1. **Assert env policy** — `GITHUB_TOKEN` present, `AZURE_AI_MODEL_DEPLOYMENT_NAME` absent. Hard-fail on either.
2. **Warm singleton `CopilotClient`** via `copilot_runtime.bootstrap()`. Authenticate with `GITHUB_TOKEN`. Construct with `load_prompts=False` (Copilot SDK does not use MCP prompts and the Toolbox does not serve `prompts/list`). Keep warm for process lifetime — never destroy/recreate within a Foundry session.
3. **Open MCP session to Toolbox** via `workiq/mcp_bridge.py`. POST `initialize` → capture `mcp-session-id` from response headers → POST `notifications/initialized`. Keep one `httpx.AsyncClient` open for the process. Use `DefaultAzureCredential` against scope `https://ai.azure.com/.default`. Every request carries header `Foundry-Features: Toolboxes=V1Preview`.
4. **`tools/list` once** against the Toolbox. Cache the schemas in memory. Always query the live endpoint at boot — do not hard-code tool schemas.
5. **Wrap each MCP tool as a Copilot `Tool`.** Sanitise the name (`.` and `-` → `_`) at this boundary only — preserve the original name for MCP `tools/call`. Copy description + `inputSchema`. Handler closes over `McpBridge.call_tool` and the per-invocation OBO context.
6. **Register the tool handler** on the Copilot session so the bridge receives every tool call with the visiting user's delegated credential.
7. **Start the Invocations server** via `azure-ai-agentserver-invocations`. Wire `/invocations` to `orchestrator.handle_invocation(action, payload, obo_context)`. The server emits root OTel spans automatically; children come from `observability.span(...)`.

### 11.6 `McpBridge` shape

Reference implementation: [architecture/samplecode_toolbox.py](architecture/samplecode_toolbox.py). The class lives at `agent/src/charter_agent/workiq/mcp_bridge.py` and must expose at least:

```python
class McpBridge:
    def __init__(self, endpoint: str, token: str) -> None: ...
    async def initialize(self) -> str: ...                       # returns server name
    async def list_tools(self) -> list[dict]: ...                # tools/list
    async def call_tool(self, name: str, arguments: dict) -> str: ...  # tools/call, ALWAYS streamed
    async def close(self) -> None: ...
```

Plus module-level helpers:

- `_get_toolbox_token() -> str` — `DefaultAzureCredential` against `https://ai.azure.com/.default`.
- `_get_toolbox_headers(token: str) -> dict` — `Authorization`, `Content-Type: application/json`, mandatory `Foundry-Features: Toolboxes=V1Preview`.
- `_make_copilot_tools(bridge: McpBridge, mcp_tools: list[dict]) -> list[Tool]` — name sanitisation + handler closures.

Hard constraints (don't try alternatives): `tools/call` must be streamed (`stream=True`); `send_ping()` returns 500 and must not be called; do not enumerate `prompts/list`.

### 11.7 Frontend BFF contract

The FastAPI BFF at `frontend/backend/` is the **only** caller of the agent's `/invocations`. On every request:

| Concern | Rule |
|---|---|
| Routing | URL `/p/{project_id}` → BFF extracts `project_id` and uses it as both the session key and the isolation key. |
| Header `x-ms-chat-isolation-key` | Always `= project_id` (overrides any per-user scoping; collaborators share one Foundry session and one `$HOME`). |
| Header `Authorization` | `Bearer {user_obo_token}` for the visiting user (MSAL flow). The agent uses this to call WorkIQ in OBO context. |
| Env `FOUNDRY_AGENT_SESSION_ID` | Set in the per-request Foundry client call to `project_id`. |
| Auth | `msal` (BFF flow), signed-cookie session via `itsdangerous`. Interactive fallback if Conditional Access blocks; surface CA blocks as a dedicated exception kind in state. |
| Dashboard response | Every state-mutating verb returns `dashboard` in the envelope; the SPA refreshes from that payload (no separate GET). |
| SSE | `coordinator_chat` streams Copilot `SessionEvent`s as SSE `data:` frames terminated by `event: done`. Wire SSE pass-through from BFF to SPA. `render_dashboard` does **not** stream. |
| Cold-start UX | Show "warming up…" spinner for 2–5s after 15-min idle on first verb. |
| Per-role filter | Coordinator sees all; owner sees only their tasks; observer sees summary only. Enforced server-side by the `render-dashboard` skill. |

### 11.8 Test matrix

| Layer | What to test | Gate |
|---|---|---|
| `state.py` | Atomic write (temp+rename), Pydantic ↔ JSON round-trip, NDJSON append-only-ness | always |
| `workiq/` wrappers | Correct MCP call shape, OBO propagation, response parsing (`respx` mocks) | always |
| `workiq/mcp_bridge.py` | Name sanitisation (`.`/`-` → `_`), Copilot tool-call → MCP routing with OBO | always |
| `capture/handlers/*` | Cursor correctness (no missed/duplicated events across two polls), author filtering | always |
| `capture/` classifier | Golden-file tests against ≥30 labelled fixture events from the board-pack scenario | `RUN_COPILOT_TESTS=1` |
| `status/triangulate.py` | Spec §8.3 truth table — parameterised across all combos | always |
| `actions/` | Double-execute is idempotent no-op; dismissed cannot be re-approved; only coordinator OBO accepted | always |
| `codegen/` | Generated `consolidator.py` passes smoke-fixture signature check; failed generation retries exactly once then raises | `RUN_CODEGEN_TESTS=1` |
| `copilot_runtime.py` | Boot env-var assertions; warm-client reuse within same Foundry session | always |
| `charter/` | Ratification rejects invalid Charters; amendment increments `version`; orphan-dependency detection | always |
| E2E (Phase 4+) | Bundled sample meeting-notes scenario end-to-end against test M365 tenant + dev Foundry project | manual, phase-gated |

Test infra: `pytest`, `pytest-asyncio`, `respx`, `freezegun`, `import-linter` (CI gate enforcing `copilot_runtime.py` as sole importer of `CopilotClient`).

### 11.9 Dependency manifest

Pin these in `agent/pyproject.toml`. Do not substitute without an ADR.

**Agent runtime:** `azure-ai-agentserver-invocations`, `github-copilot-sdk`, `azure-identity`, `httpx`, `pydantic` v2, `python-docx`, `openpyxl` (pinned at agent level so generated `consolidator.py` cannot invent deps), `opentelemetry-api`, `opentelemetry-sdk`, `azure-monitor-opentelemetry-distro`, `azure-ai-projects` (optional, future portal calls).

**Agent dev/CI:** `import-linter`, `ruff`, `pyright`, `pytest`, `pytest-asyncio`, `respx`, `freezegun`.

**Frontend BFF (`frontend/backend/pyproject.toml`):** `fastapi`, `uvicorn`, `msal`, `itsdangerous`, `httpx`.

**Frontend SPA (`frontend/ui/package.json`):** `react` 18, `react-dom` 18, `typescript` 5, `vite` 5, `react-router-dom` 6, `eslint`, `prettier`. CSS Modules. No TanStack Query / SWR / Tailwind / CSS-in-JS in MVP. Native `fetch` wrapped in a thin `bff.ts`. MVP component count: ~10; revisit when it exceeds ~30.

### 11.10 Phases

Per [spec §9](functional-specs/project_workspace_spec.md). Do not skip ahead; each phase produces a runnable artifact.

| Phase | Scope | Verbs newly exercised | Demoable outcome |
|---|---|---|---|
| 1. Skeleton | `__main__`, `copilot_runtime` (warm-only), `orchestrator`, `state` (counter only), frontend skeleton | `echo` | Two browsers, same `project_id`, same counter; Copilot session resumed across requests |
| 2. Charter & kickoff | `charter/`, `kickoff/`, `workiq/` (Mail/Files/Teams/Tasks), `project-kickoff` skill | `propose_charter`, `ratify_charter` | Real M365 fan-out for the bundled sample scenario |
| 3. Skills + exceptional codegen | All `agent/skills/*`, `codegen/` (consolidator only), `consolidation/` | (exercised by Phase 2/4 paths) | Skills auto-loaded; generated `consolidator.py` visible in sandbox |
| 4. Capture loop | `capture/`, `status/triangulate`, `actions/` (draft only) | `render_dashboard` | Live status changes as deliveries land on channels |
| 5. Dashboard + approvals | SPA, exceptions panel, `actions/execute`, `actions/dismiss`, coordinator chat | `execute_suggested`, `dismiss_suggested`, `coordinator_chat` | Approve real Teams nudge sent as coordinator OBO |
| 6. Consolidation + closure | `consolidation/`, `charter/amend`, close path, `amend-charter` skill | `amend_charter`, `close_project`, `override_capture` | Reconciliation fires; project closes; deliverable on SharePoint |
| 7. Hardening | All modules; idempotency edge cases, CA-block recovery, audit-log review | — | Production-ready: tests, recovery, telemetry complete |

---

## 12. Quick links

- [Requirement spec](functional-specs/project_workspace_spec.md)
- [Architecture & design](architecture/architecture_and_design.md)
- [References](functional-specs/references.md)
- [Sample meeting-notes file](test-fixtures/sample-meeting-notes.md) — **test input only**, one of many possible project shapes; not a normative scenario
- [Dashboard UI mock](test-fixtures/dashboard-mock.html) — illustrative reference for spec §5.6, depicting how the dashboard would render *for that particular sample's data*. Not the implementation, not the design contract.
- [test-fixtures/README.md](test-fixtures/README.md) — banner explaining the non-normative status of everything in that folder
- [Toolbox + Copilot-SDK reference sample](architecture/samplecode_toolbox.py)
- [agentskills.io specification](https://agentskills.io/specification) — the format every skill in `agent/skills/` must conform to (invariant 1)
- [agentskills.io client showcase](https://agentskills.io/clients) — clients (Claude Code, VS Code, Goose, Gemini CLI, Kiro, fast-agent, …) where a SKILL.md authored here can be loaded for isolated testing
- [skills-ref validator](https://github.com/agentskills/agentskills/tree/main/skills-ref) — used in CI to gate PRs that touch `agent/skills/`
