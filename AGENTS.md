# AGENTS.md — charter-agent

> Read this before every change. It is the operating contract for any coding agent (GitHub Copilot, Claude, Cursor, etc.) working in this repository. Humans should also read it before opening a PR.

This file follows the [agents.md](https://agents.md) convention. It is intentionally short and pointer-heavy. The substance lives in the specs it points to.

---

## 1. What this project is

An **agent-orchestrated project coordination workspace**. A senior coordinator (Chief of Staff, programme manager, deal lead, audit lead) describes a cross-functional deliverable in natural language. A Microsoft Foundry hosted agent decomposes it into a **Project Charter**, kicks off the workstreams across Microsoft 365 (SharePoint, Teams, email, Outlook tasks), watches for deliveries across heterogeneous channels, infers status, drafts nudges and reassignments for human approval, and consolidates the final artifact.

One shared dashboard URL per project. Every stakeholder sees live status. When the project closes, the workspace dissolves.

The **canonical scenarios** are listed in [functional-specs/project_workspace_spec.md §2.4](functional-specs/project_workspace_spec.md). The kickoff trigger for the first end-to-end test comes from [functional-specs/project_lumen_meeting_notes.md](functional-specs/project_lumen_meeting_notes.md).

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

1. **Generic over specific.** The agent's code is project-shape-agnostic. *All* per-project variety lives in (a) the **Project Charter** (`$HOME/charter.json` in the session sandbox), (b) the **Copilot SDK skills** under `agent/skills/` (auto-loaded by the Copilot runtime; shape the agent's reasoning declaratively per workflow), and (c) — only when deterministic running code is genuinely required — a **Copilot-generated `consolidator.py`** in `$HOME/code/`. Do not hard-code domain logic — board pack, audit, escalation, budget, etc. — anywhere in the agent itself. If a feature seems to need it, the answer is almost always "extend the Charter schema, add or refine a skill, or (last resort) regenerate `consolidator.py`."
2. **WorkIQ is delegated-only.** Every WorkIQ call runs in the **visiting user's OBO context**, never as the agent's managed identity. Application-only auth is not supported. See [spec §10.1](functional-specs/project_workspace_spec.md). This forces invariants 3 and 4 below.
3. **No background workers, no cron, no schedulers.** The agent only runs when a user hits `/invocations`. There is no autonomous wake-up loop. Anything that *feels* like it needs scheduling is wrong — re-think it as "runs on next visit."
4. **Human-in-the-loop for every outbound action.** The agent drafts; the coordinator approves; the agent sends in the **coordinator's OBO context** (not as a bot). There is no `auto_approve` mode. Do not add one.
5. **State lives in `$HOME`, period.** No external database, queue, cache, or event bus. The Foundry per-session microVM `$HOME` (Charter, `state.json`, `activity.json`, Copilot-generated code) is the entire project store. The frontend is a renderer; it holds no state.
6. **Charter immutability outside the ratification flow.** Only the `kickoff` and `amend_charter` code paths may write `charter.json`. Both must run through coordinator ratification. Increment `version` on every amendment.
7. **Use Invocations, not Responses.** The Foundry protocol choice is Invocations (we manage history ourselves). Don't introduce Responses-protocol code.
8. **Channel-watchers are a registry.** New `watch_channel.kind` values must plug into the registry described in [architecture §6.2](architecture/architecture_and_design.md). Never `if channel.kind == "...":` switches scattered in agent code.
9. **Idempotency on every outbound side-effect.** Every suggested action has a UUID; `state.executed_action_ids` is the gate. A double-approve must not double-send.
10. **No ports exposed from the sandbox.** The frontend Container App is the only public surface. The agent serves only `/invocations`.
11. **Single agent runtime — GitHub Copilot SDK.** The agent is a thin shell around a `CopilotClient` from the [`github-copilot-sdk`](https://pypi.org/project/github-copilot-sdk/) PyPI package, used **in-process** (`from copilot import CopilotClient`; no CLI, no subprocess). The Copilot session and the Foundry hosted-agent session map **1:1** keyed by `FOUNDRY_AGENT_SESSION_ID`; intelligent compaction in the SDK is the agent's conversational memory. All reasoning — charter proposal, classification, drafting nudges, coordinator chat, consolidation orchestration, and the (exceptional) generation of `consolidator.py` — runs through that single client. Auth is via `GITHUB_TOKEN` (a fine-grained GitHub PAT with *Copilot Requests → Read-only*) so the SDK runs on GHCP's default model (Claude Opus 4.7 today). **Do not** set `AZURE_AI_MODEL_DEPLOYMENT_NAME` in the agent's process env — the Copilot SDK would silently flip to the Foundry-model backend ([sample README](https://github.com/microsoft-foundry/foundry-samples/blob/main/samples/python/hosted-agents/bring-your-own/invocations/github-copilot/README.md): *"If both are set → the Foundry model takes precedence"*). The `gpt-5.x` deployments in the Foundry project remain reachable as named tools if a skill ever needs them, but they are not the agent's runtime. See [§4.2](#42-model-assignment-policy).

A longer, scenario-flavoured version of these invariants is in [spec §10 "Things easy to get wrong"](functional-specs/project_workspace_spec.md). Read it.

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
- Do **not** spawn a second LLM call path (a separate Anthropic API key, a different OpenAI account, a parallel MAF reasoning helper) without an ADR and an update to invariant 11. If a skill needs a different model for a specific step, expose it as a **named Copilot tool** that calls a Foundry `gpt-5.x` deployment with the agent's Managed Identity — do not create a second runtime.

**Where this is enforced in code:**

- `agent/src/charter_agent/copilot_runtime.py` is the **only** module allowed to instantiate `CopilotClient`. Enforced via `import-linter` contract.
- `agent/src/charter_agent/codegen/` (for `consolidator.py` generation) calls into `copilot_runtime.py` to acquire a session — it does not construct its own client.
- The agent boot sequence asserts: `GITHUB_TOKEN` present, `AZURE_AI_MODEL_DEPLOYMENT_NAME` absent. Refuses to start if either check fails.

---

## 5. Repository layout (target)

The repo is currently spec-only. As code lands, follow this layout. Update this section in the same PR if you add a new top-level directory.

```
charter-agent/
├── AGENTS.md                          ← you are here
├── README.md                          ← human-facing intro (TBD)
├── functional-specs/                  ← the "what & why" + references
│   ├── project_workspace_spec.md
│   ├── project_lumen_meeting_notes.md
│   └── references.md
├── architecture/                      ← the "how"
│   ├── architecture_and_design.md
│   └── samplecode_toolbox.py          ← portal-generated Toolbox + Copilot-SDK reference
├── agent/                             ← the Foundry hosted agent
│   ├── Dockerfile
│   ├── agent.yaml
│   ├── pyproject.toml
│   ├── skills/                        ← Copilot SDK skills (auto-loaded at process start)
│   │   ├── project-kickoff/SKILL.md
│   │   ├── status-refresh/SKILL.md
│   │   ├── capture-classify/SKILL.md
│   │   ├── compliance-check/SKILL.md
│   │   ├── draft-outbound/SKILL.md
│   │   ├── consolidate/SKILL.md
│   │   └── amend-charter/SKILL.md
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

Two folders that are explicitly *not* in the repo: a `database/` and a `worker/` directory. If you find yourself wanting either, see invariants 3 and 5.

---

## 6. Conventions

- **Language**: Python 3.12 for the agent and the BFF. Type-hinted, `ruff` + `pyright` clean. No untyped public functions.
- **Logging**: Always go through `observability.py` — it emits both an OTel span and an `activity.json` entry. Never call `print()` or bare `logging.info` in agent code.
- **WorkIQ access**: Only through `agent/src/charter_agent/workiq/`. No direct MCP calls from orchestrator/kickoff/capture code. This makes mocking in tests possible. The `McpBridge` that exposes WorkIQ tools to the Copilot session also lives in this module — see [architecture/samplecode_toolbox.py](architecture/samplecode_toolbox.py) for the reference implementation.
- **Copilot SDK runtime**: Only through `agent/src/charter_agent/copilot_runtime.py`. One `CopilotClient` instance per agent process (kept warm). One Copilot session per Foundry session, resumed via `FOUNDRY_AGENT_SESSION_ID` — never recreated within the same Foundry session. The (exceptional) `consolidator.py` codegen path in `codegen/` borrows a session from `copilot_runtime.py`; it does not construct its own client.
- **Skills**: Project-workspace specialisation lives in `agent/skills/*/SKILL.md`, auto-loaded by the Copilot SDK at process start. Skill changes are code changes — reviewed, versioned, and shipped via the agent image. Do not write per-project skills; the skills set is generic, the Charter is what the skills reason against.
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

- [ ] Does the change preserve invariants 1–11 in §3?
- [ ] If it adds a domain-specific behaviour, has it been moved into the Charter schema or the Copilot prompt instead?
- [ ] If it touches `$HOME`, does it route through `state.py` and emit an activity-log entry?
- [ ] If it calls WorkIQ, is the call going through `workiq/` and explicitly in the visiting user's OBO context?
- [ ] If it adds an outbound action, does it have a UUID and a check against `executed_action_ids`?
- [ ] If it adds a new `watch_channel.kind`, is it registered in the channel registry (not switched on inline)?
- [ ] If it adds a new `/invocations` action verb, is the action contract documented in [architecture §7](architecture/architecture_and_design.md)?
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

## 11. Quick links

- [Requirement spec](functional-specs/project_workspace_spec.md)
- [Architecture & design](architecture/architecture_and_design.md)
- [References](functional-specs/references.md)
- [Project Lumen sample meeting notes (kickoff trigger for first E2E test)](functional-specs/project_lumen_meeting_notes.md)
