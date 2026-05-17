# Reference Documentation — Foundry Hosted Agents, WorkIQ, GitHub Copilot SDK

Organised by area. The first two sections cover the priority topics — shared session state and the VS Code / hosted-agent development experience — followed by everything else used in shaping the architecture.

---

## 1. Shared session state and isolation keys (multi-user access pattern)

The load-bearing capability for the project workspace design — multiple users hitting the same project session via shared chat-isolation key.

- **Manage hosted agent sessions** — official docs on session APIs, isolation keys, version pinning, and session lifecycle.
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/manage-hosted-sessions

- **Foundry Hosted Agents — Sessions, State & the Sandbox** (Ankit Bhargava part 2) — clearest practical explanation of `x-ms-user-isolation-key` and `x-ms-chat-isolation-key`, including behaviour when multiple users share a chat-isolation key. This is the source that confirmed the multi-user shared-workspace pattern.
  https://ankitbko.github.io/blog/2026/05/hosted-agents-part-2/

- **Hosted agents in Foundry Agent Service (preview)** — canonical conceptual overview including session lifetime, idle behaviour, and protocol details.
  https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents

- **Introducing the new hosted agents in Foundry Agent Service** — announcement blog with per-session microVM, scale-to-zero, and OBO identity-passthrough details.
  https://devblogs.microsoft.com/foundry/introducing-the-new-hosted-agents-in-foundry-agent-service-secure-scalable-compute-built-for-agents/

---

## 2. Developing Foundry hosted agents — VS Code, azd, and the SDKs

The actual build environment.

- **Quickstart: Deploy your first hosted agent** — start here. Walks through the VS Code extension and `azd` workflow end-to-end.
  https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/quickstart-hosted-agent

- **Deploy a hosted agent** — deployment reference: container image, ACR push, version registration, polling for active status.
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent

- **Manage hosted agents** — managing versions, traffic routing, logs (`logstream` SSE endpoint), and the `azd ai agent monitor` command.
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/manage-hosted-agent

- **Microsoft Foundry Hosted Agents — What, Why, Protocols & Your First Deployment** (Ankit Bhargava part 1) — practitioner-oriented intro. Covers `FOUNDRY_*` environment variables and Responses vs Invocations protocol choice.
  https://ankitbko.github.io/blog/2026/05/hosted-agents-part-1/

---

## 3. Microsoft Agent Framework — the host runtime

The **host agent runtime** for this project is Microsoft Agent Framework (`agent-framework` + `agent-framework-azure-ai`), running on a Foundry `gpt-5.x` deployment authenticated by the Foundry-assigned Managed Identity. MAF owns `/invocations` (served by `azure-ai-agentserver-invocations`), the conversational thread (`AgentSession` persisted to `$HOME`), skill loading, and native `MCPTool` dispatch against the Foundry Toolbox. See [AGENTS.md §3 invariant 12](../AGENTS.md) and [§4](../AGENTS.md#4-technology-choices-locked).

- **Foundry Hosted Agents (Agent Framework docs)** — `ResponsesHostServer` and `InvocationsHostServer` wrappers, Python and .NET examples.
  https://learn.microsoft.com/en-us/agent-framework/hosting/foundry-hosted-agent

- **Agent Chat History and Memory** — `AgentSession` serialisation/deserialisation, in-service vs in-memory storage of chat history. This is the mechanism we use to persist the host runtime's thread inside the Foundry per-session microVM `$HOME`.
  https://learn.microsoft.com/en-us/agent-framework/user-guide/agents/agent-memory

- **Step 4: Memory & Persistence** — practical session-state and context-provider patterns.
  https://learn.microsoft.com/en-us/agent-framework/get-started/memory

- **`MCPTool` (Agent Framework MCP client)** — the native MCP client we use to talk to the Foundry Toolbox. Replaces the hand-rolled `McpBridge` in the portal-generated `architecture/samplecode_toolbox.py`. Handles `initialize` / `mcp-session-id` / `notifications/initialized` / `tools/list` / `tools/call` / approval-item plumbing. Headers and per-call header injection (used to stamp the coordinator's WorkIQ token on each call — see [AGENTS.md invariant 3](../AGENTS.md#3-non-negotiable-architectural-invariants)) are first-class.
  https://learn.microsoft.com/en-us/agent-framework/user-guide/agents/tools/mcp

- **GitHub Copilot Agents (Agent Framework docs)** — `CopilotClient.AsAIAgent()` / `GitHubCopilotAgent`, the MAF wrapper around the Copilot SDK we use for the **codegen sub-agent** (see §9 for the SDK itself and the dual-runtime rationale).
  https://learn.microsoft.com/en-us/agent-framework/agents/providers/github-copilot

- **Getting Started with Foundry Hosted Agents** (Valentina Alto, Medium) — walk-through of going from a local agent to a deployed hosted one, with the same endpoint serving both portal and custom UIs.
  https://valentinaalto.medium.com/getting-started-with-foundry-hosted-agents-394959230136

- **Hosting an AG-UI Agent on Microsoft Foundry** (baeke.info) — worked example of the Invocations protocol with a custom client. Important warning included: you can't choose your own endpoints freely.
  https://baeke.info/2026/04/26/hosting-an-ag-ui-agent-on-microsoft-foundry/

---

## 4. Identity, OBO, and agent permissions

- **Agent identity concepts in Microsoft Foundry** — Entra Agent ID, OAuth token exchange flow, blueprints, federated credentials.
  https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-identity

- **Conditional Access for Agent Identities in Microsoft Entra** — OBO flow diagram, how Conditional Access applies to user-delegated and application-only agent access. Worth reading before building the Container App's auth.
  https://learn.microsoft.com/en-us/entra/identity/conditional-access/agent-id

- **User Identity Passthrough for Hosted Agents Calling a Custom MCP Server** (Microsoft Q&A) — nuance: code-first hosted agents have different identity-passthrough behaviour than portal-created prompt agents. Useful if implementation hits edge cases.
  https://learn.microsoft.com/en-au/answers/questions/5872669/user-identity-passthrough-for-hosted-agents-callin

---

## 5. WorkIQ MCP servers

- **Work IQ MCP overview (preview)** — canonical overview, including licensing requirement and tenant enablement script.
  https://learn.microsoft.com/en-us/microsoft-agent-365/tooling-servers-overview

- **Work IQ API quickstart (preview)** — public-client and confidential-client setup paths, including the OBO flow for server-side agents.
  https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq-api-quickstart

- **Microsoft Work IQ API (preview)** — API reference, including the explicit statement that Work IQ uses delegated auth only and application-only is not supported.
  https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq-api-overview

- **Work IQ MCP overview (Copilot Studio docs)** — same overview from the Copilot Studio angle, including admin governance details.
  https://learn.microsoft.com/en-us/microsoft-copilot-studio/use-work-iq

- **microsoft/work-iq-samples** (GitHub) — canonical sample repo with tenant admin setup scripts and end-to-end .NET samples.
  https://github.com/microsoft/work-iq-samples

- **microsoft/work-iq-mcp User Guide** (DeepWiki) — covers the GitHub Copilot CLI plugin pattern and the delegated-permissions-only constraint clearly.
  https://deepwiki.com/microsoft/work-iq-mcp/2-user-guide

- **Use Work IQ MCP servers in 3rd party agents** (candede.com blog) — third-party path, for contrast. NOT the path to take since you're using Foundry-hosted agents with portal-configured connections, but useful to understand the difference.
  https://candede.com/articles/use-work-iq-mcp-servers-from-3rd-party-apps

---

## 6. GitHub Copilot SDK

- **microsoft/copilot-sdk** (GitHub) — canonical SDK repo with installation, language coverage, BYOK details.
  https://github.com/github/copilot-sdk

- **Build an agent into any app with the GitHub Copilot SDK** (GitHub blog) — launch announcement. Useful for understanding the SDK's positioning as a runtime, not an API client.
  https://github.blog/news-insights/company-news/build-an-agent-into-any-app-with-the-github-copilot-sdk/

- **GitHub Copilot Agents (Agent Framework docs)** — Microsoft Agent Framework integration, with `CopilotClient.AsAIAgent()` and `GitHubCopilotAgent` examples. Most relevant entry point for this build.
  https://learn.microsoft.com/en-us/agent-framework/agents/providers/github-copilot

- **Build AI Agents with GitHub Copilot SDK and Microsoft Agent Framework** (devblogs) — practical guide to combining the two, with MCP server config including remote HTTP servers like Microsoft Learn.
  https://devblogs.microsoft.com/semantic-kernel/build-ai-agents-with-github-copilot-sdk-and-microsoft-agent-framework/

- **Agentify Your App with GitHub Copilot's Agentic Coding SDK** (Machine Learning Mastery) — Python-focused walkthrough including custom tools and Pydantic-typed parameters.
  https://machinelearningmastery.com/agentify-your-app-with-github-copilots-agentic-coding-sdk/

- **The GitHub Copilot SDK for .NET** (the-runtime.dev) — .NET-specific patterns, BYOK configuration, event taxonomy.
  https://the-runtime.dev/articles/github-copilot-sdk-dotnet/

- **The GitHub Copilot SDK — Agents for Every App** (htek.dev) — overview with sample apps and the MCP-under-the-hood point.
  https://htek.dev/articles/github-copilot-sdk-agents-for-every-app/

- **Building Agents with GitHub Copilot SDK** (Microsoft Community Hub) — worked example of an automated daily-update tracker. Useful as a reference for the kind of orchestration to build.
  https://techcommunity.microsoft.com/blog/azuredevcommunityblog/building-agents-with-github-copilot-sdk-a-practical-guide-to-automated-tech-upda/4488948

- **Start Copilot cloud agent tasks via the REST API** (GitHub changelog) — for context, the *other* Copilot agent path (cloud agent, not the SDK runtime). Mention to avoid confusing the two.
  https://github.blog/changelog/2026-05-13-start-copilot-cloud-agent-tasks-via-the-rest-api/

---

## 7. Memory and persistence patterns

- **Microsoft Foundry: Unlock Adaptive, Personalized Agents with User-Scoped Persistent Memory** (Microsoft Community Hub) — reference architecture for user-scoped memory using Cosmos DB. Useful if project state evolves to need cross-project memory of user preferences.
  https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/microsoft-foundry-unlock-adaptive-personalized-agents-with-user-scoped-persisten/4505622

---

## 8. Foundry Toolboxes (MCP-compatible tool bundling)

The path we are taking: build a single Foundry **Toolbox** in the project portal that bundles all WorkIQ MCP servers (Mail, Calendar, Files, Teams, `workiq.ask`) behind one MCP-compatible endpoint, then point the hosted agent at that one endpoint instead of configuring each WorkIQ MCP server individually. The Toolbox centrally handles credential injection, token refresh, and policy enforcement for everything inside it; the agent authenticates only to the Toolbox endpoint.

- **Connect agents to Model Context Protocol servers** (Microsoft Learn) — the canonical reference. Contains the dedicated section **"Use Foundry Toolboxes as MCP endpoints"** with the endpoint URL format, plus full Python/C#/TS samples for declaring an `MCPTool` against a Toolbox endpoint and handling `mcp_approval_request` items.
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/model-context-protocol

- **Create and use a Foundry Toolbox** (Microsoft Learn) — the dedicated Toolbox setup guide referenced from the doc above. Covers prerequisites, creating a Toolbox, adding tools (Web Search, Code Interpreter, File Search, Azure AI Search, MCP servers, OpenAPI tools, A2A), versioning, and promoting a version to default.
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/toolbox

- **MCP server authentication for Foundry agents** (Microsoft Learn) — covers key-based, Microsoft Entra, and OAuth identity passthrough. Confirms that for a Toolbox, the agent uses `DefaultAzureCredential` against the Toolbox endpoint and the Toolbox manages per-tool credentials.
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/mcp-authentication

- **MCP tool REST reference (`OpenAIMCPTool`)** (Microsoft Learn) — the wire-level shape for the `mcp` tool declaration (`server_url`, `server_label`, `allowed_tools`, `require_approval`, `project_connection_id`).
  https://learn.microsoft.com/en-us/azure/foundry/reference/foundry-project-rest-preview#openaimcptool

### Quick-reference: Toolbox endpoint URL format

From the Microsoft Learn doc above:

- **Consumer endpoint** (use this in production — always serves the promoted default Toolbox version):
  `{project_endpoint}/toolboxes/{toolbox_name}/mcp?api-version=v1`
- **Version-specific endpoint** (use this in dev/test to validate a new Toolbox version before promotion):
  `{project_endpoint}/toolboxes/{toolbox_name}/versions/{version}/mcp?api-version=v1`

Wire it up exactly like any other remote MCP server in the agent's tool declaration — for example, in Python:

```python
from azure.ai.projects.models import MCPTool

workiq_toolbox = MCPTool(
    server_label="workiq",                # keep unique per agent
    server_url="https://<your-project-endpoint>/toolboxes/workiq/mcp?api-version=v1",
    require_approval="never",             # see notes below
    project_connection_id="<toolbox connection name>",
)
```

`server_label` should stay stable for an agent even when you switch Toolbox versions in dev (the version-switch happens via `server_url`, not the label).

### Approval policy note for our build

Foundry's MCP tool surfaces an `mcp_approval_request` for any tool call that requires approval, and the agent must reply with an `mcp_approval_response` before the call proceeds. **Do not** rely on this as the human-in-the-loop for WorkIQ *send* operations (mail / Teams) — our architecture handles human approval at a higher layer (the `actions/` module's `SuggestedAction` lifecycle, where the *coordinator* approves the drafted message in the dashboard before the agent sends it in her OBO context). Recommended Toolbox configuration:

- WorkIQ **read** tools (Mail/Calendar/Files/Teams reads, `workiq.ask`): `require_approval="never"` — they run inside the on-visit capture loop and would create unworkable UX otherwise.
- WorkIQ **send/write** tools: also `require_approval="never"` at the MCP layer, **but** the only code path that invokes them is `actions/execute_suggested`, which is itself gated by coordinator approval in the dashboard. Document this in code-comments at the call site.

### Known limitation worth flagging

The doc above lists a **non-streaming MCP tool call timeout of 100 seconds**. The capture loop should fan out channel-handler polls concurrently (asyncio gather) rather than serially, and any single WorkIQ call must complete well under that bound. If a `workiq.ask` natural-language query starts hitting the limit, decompose it.

### Local working sample

A working, portal-generated sample lives in the repo at [`../architecture/samplecode_toolbox.py`](../architecture/samplecode_toolbox.py). It pins down the version-specific endpoint URL, the `DefaultAzureCredential` + `https://ai.azure.com/.default` token scope, the required `Foundry-Features: Toolboxes=V1Preview` header, the MCP `initialize`/`mcp-session-id`/`tools/list`/`tools/call` flow, and the Copilot-SDK tool-name sanitization rule (no `.` or `-`). The implementation in `agent/src/charter_agent/workiq/` should follow this sample for the Toolbox connection.

**Standing rule.** The sample is a *starting reference*, not a contract. For anything beyond the calls shown in it \u2014 a new tool, a changed schema, a new MCP method, a bumped `protocolVersion`, a renamed header \u2014 introspect the **live Toolbox MCP endpoint** (`tools/list`, `initialize` capabilities) at runtime rather than coding against a stale snapshot. Treat Microsoft Learn (the links above) as the next-most-authoritative source after the live server. If the sample needs to change because the portal regenerated a newer one, replace it and update [`../architecture/architecture_and_design.md` \u00a78.1](../architecture/architecture_and_design.md) in the same PR.

---
## 9. GitHub Copilot SDK as the codegen sub-agent (dual-runtime)

The Copilot SDK is **not** the host runtime in this project. It is the **codegen sub-agent**: a second, narrowly-scoped MAF agent (`GitHubCopilotAgent` / `CopilotClient.AsAIAgent()`) that runs on GHCP's default model (currently Claude Opus 4.7) and is invoked **only** by the `consolidate` skill, **only** to generate `$HOME/code/consolidator.py`. See [AGENTS.md invariant 12](../AGENTS.md#3-non-negotiable-architectural-invariants) and [§4.2](../AGENTS.md#42-model-assignment-policy).

The canonical reference for wiring the SDK into a Foundry hosted agent over the Invocations protocol is the Microsoft sample below — read it for the protocol shape and the credential-isolation hazard, then ignore the parts that assume the SDK is the *only* runtime in the agent:

- **`microsoft-foundry/foundry-samples` — `github-copilot` Invocations sample** (GitHub).
  https://github.com/microsoft-foundry/foundry-samples/blob/main/samples/python/hosted-agents/bring-your-own/invocations/github-copilot/README.md

What it pins down for our build (don't re-derive any of this from first principles):

- **Copilot SDK is the [`github-copilot-sdk`](https://pypi.org/project/github-copilot-sdk/) PyPI package, used in-process** (`from copilot import CopilotClient`). No CLI, no subprocess.
- **Invocations protocol server** is the [`azure-ai-agentserver-invocations`](https://pypi.org/project/azure-ai-agentserver-invocations/) package. It is what serves `POST /invocations` inside the hosted agent. Both the host runtime (MAF) and the codegen sub-agent (Copilot SDK) sit on top of this one server — only one process, one Invocations server, two MAF agents inside it.
- **Two authentication / model backends** the Copilot SDK can sit on top of, selected by env vars:
  | Backend | Required env vars | Credential | Effective model |
  |---|---|---|---|
  | **GitHub Copilot** (the one we use for codegen) | `GITHUB_TOKEN` | Fine-grained GitHub PAT, *Copilot Requests → Read-only* permission. Classic `ghp_` tokens are **not** supported — must be `github_pat_`, `gho_`, or `ghu_`. | Whatever GHCP's default model is (currently Claude Opus 4.7). |
  | **Foundry model** (we explicitly do **not** use — this is the **host** runtime's job via MAF, not the codegen sub-agent's) | `FOUNDRY_PROJECT_ENDPOINT` + `AZURE_AI_MODEL_DEPLOYMENT_NAME` | `DefaultAzureCredential` / Managed Identity | The named Azure OpenAI deployment. |
  
  **If both env-var sets are present, the Foundry backend silently wins.** In our dual-runtime world *both* env vars are deliberately present — the host runtime needs `AZURE_AI_MODEL_DEPLOYMENT_NAME`. To defeat the silent flip, `runtime/copilot_codegen.py` reads `GITHUB_TOKEN` from the OS env and passes it to `CopilotClient(github_token=…)` **as a constructor argument**, so the SDK's backend selector sees an explicit token and routes to GHCP regardless of what else is in the env. The boot sequence asserts both env vars are present (see [AGENTS.md §4.2](../AGENTS.md#42-model-assignment-policy)).
- **Session persistence is first-class.** The hosted agent's microVM keeps each MAF agent's session in memory and resumes it across `/invocations` calls via `FOUNDRY_AGENT_SESSION_ID`. Our `runtime/copilot_codegen.py` keeps one `CopilotClient`-backed `GitHubCopilotAgent` warm for the lifetime of the process and uses it only for short-lived codegen turns; nothing else in the system shares that thread.
- **Skills.** The Copilot SDK has its own `skills/*/SKILL.md` auto-loader. We do **not** rely on it — our host runtime is MAF, and skills are loaded by `runtime/skill_loader.py` and injected into the MAF `ChatAgent`. See [§10](#10-agent-skills-format--the-open-agentskillsio-specification).
- **Streaming.** Each MAF agent yields response chunks back to the caller as an SSE `data:` frame, terminated by `event: done`. The dashboard's `render_dashboard` verb does not stream, but `coordinator_chat` should — wire SSE through to the BFF when implementing that verb. The codegen sub-agent is invoked through a tool, not streamed to the user.
- **Build/deploy gotcha.** Container images must be `linux/amd64`. On Apple Silicon, use `docker build --platform=linux/amd64 ...`. `azd deploy` does an ACR remote build and avoids this.

**Standing rule — do not move work onto the codegen sub-agent.** Quality wishes ("Opus would phrase this nudge better") are not a reason to widen its remit. If a non-codegen step needs a different model, surface that model as a named MAF tool that the host `ChatAgent` can select — do not invent a second reasoning surface. See [AGENTS.md invariant 12](../AGENTS.md#3-non-negotiable-architectural-invariants).

---

## 10. Agent Skills format — the open agentskills.io specification

The skills under `agent/skills/` conform to the open **[agentskills.io](https://agentskills.io/specification)** specification (Anthropic-originated, now adopted by GitHub Copilot, VS Code, Claude Code, Goose, Gemini CLI, Kiro, fast-agent, Letta, Mistral Vibe). This is invariant 1 of the project ([AGENTS.md §3](../AGENTS.md#3-non-negotiable-architectural-invariants)) and is contractually load-bearing: every `agent/skills/{name}/SKILL.md` must be a valid Agent Skill per the spec.

In this project the host runtime is MAF, not the Copilot SDK, so we do **not** rely on the Copilot SDK's built-in `skills/` auto-loader. Instead, a small in-repo loader (`runtime/skill_loader.py`, ~50 lines) reads each `agent/skills/*/SKILL.md`, validates the frontmatter against the agentskills.io shape, and injects the body into the host `ChatAgent`'s instructions / tool-selection surface at boot. Conformance to the open spec still buys us portability — the same `SKILL.md` body can be loaded by any agentskills.io client for isolated authoring/testing without standing up the full Foundry agent.

- **Specification** — directory layout, required YAML frontmatter fields (`name`, `description`), optional fields (`license`, `compatibility`, `metadata`, `allowed-tools`), naming rules (lowercase a–z / digits / hyphens, ≤64 chars, must match parent directory), description rules (≤1024 chars, must convey *what* and *when*), progressive disclosure stages (Discovery → Activation → Execution), and the optional `scripts/`, `references/`, `assets/` subdirs.
  https://agentskills.io/specification

- **Client showcase** — list of clients that load agentskills.io-compatible skills. A skill authored in this repo can be tested in Claude Code, VS Code Copilot, Goose, or Gemini CLI without modification.
  https://agentskills.io/clients

- **`skills-ref` validator** — the upstream CLI that validates a skill directory against the spec. We run `skills-ref validate ./agent/skills/*` in CI on every PR that touches the skills directory.
  https://github.com/agentskills/agentskills/tree/main/skills-ref

What this gives us in practice (and what AGENTS.md §4.3 codifies as our local conventions on top):

- **Portability** — the same `SKILL.md` body can be loaded by any agentskills.io client.
- **Auditability** — skills are versioned files reviewed in PRs, not opaque prompt strings buried in code.
- **A sharp core/skill split** — see [AGENTS.md §4.4](../AGENTS.md#44-core-code-vs-skill--the-decision-rule). Deterministic plumbing (state I/O, cursors, idempotency, transport, dispatch) stays as code; reasoning/generation/judgement is always a skill.

**Out of scope, deliberately:** Claude-Code-specific extension mechanisms — `plugins/`, per-skill `mcp.json`, slash commands — are *not* part of the agentskills.io spec and *not* used here. Tool discovery for this project is the **Foundry Toolbox over MCP** (see §8 above), which replaces per-skill `mcp.json` files with one bundled MCP endpoint (`Charter-Agent-Tools`) consumed via the host runtime's native MAF `MCPTool`.

---
## Recommended reading order

If the implementing team is new to this stack:

1. **Hosted agents conceptual overview** (Microsoft Learn) — platform model first.
2. **Ankit Bhargava part 1** — practitioner's view, especially the protocol choice.
3. **Quickstart** — actually deploy a trivial hosted agent end-to-end. Half a day, no more.
4. **Ankit Bhargava part 2** — once something's deployed, this makes the isolation-keys story land properly.
5. **agentskills.io specification (§10 above)** — read this before writing or reviewing any `SKILL.md`; it is invariant 1.
6. **Agent Framework hosting integration** — once writing real agent logic.
7. **Work IQ MCP overview** plus verification that the tenant's existing portal agents can call WorkIQ — confirm dependencies are sorted before going further.
8. **Connect agents to MCP servers** + **Create and use a Foundry Toolbox** (section 8 above) — read these before wiring WorkIQ into the agent, since the chosen integration pattern is one Foundry Toolbox endpoint that bundles all WorkIQ MCP servers, not direct per-server MCP connections.
9. **Agent Framework + GitHub Copilot integration** — the `CopilotClient.AsAIAgent()` / `GitHubCopilotAgent` wrapper. We **do** use this, but only for the **codegen sub-agent** (see §9 above). It is *not* the host runtime — the host runtime is a plain MAF `ChatAgent` over a Foundry `gpt-5.x` deployment. See [AGENTS.md §3 invariant 12](../AGENTS.md) for the dual-runtime rationale.

A small caveat: several of these sources are recent enough (particularly the May 2026 Foundry hosted agents refresh) that official Learn docs and practitioner blogs are still settling into agreement. Where they disagree, trust Microsoft Learn for capability claims and the practitioner blogs for usage patterns.