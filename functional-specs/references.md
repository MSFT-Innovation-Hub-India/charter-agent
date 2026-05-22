# Reference Documentation — Foundry Hosted Agents, WorkIQ, Microsoft Agent Framework

Organised by area. Sections cover the platform (Foundry hosted agents and the development workflow), the host runtime (Microsoft Agent Framework), identity (Entra / OBO), the data plane (WorkIQ MCP servers, wrapped in a Foundry Toolbox), and the skills format that shapes the agent's behaviour.

---

## 1. Session lifecycle and isolation keys

How a project maps to a Foundry session — chat-isolation key from `project_id`, per-session `$HOME` sandbox, 30-day lifetime, 15-minute idle timeout.

- **Manage hosted agent sessions** — official docs on session APIs, isolation keys, version pinning, and session lifecycle.
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/manage-hosted-sessions

- **Foundry Hosted Agents — Sessions, State & the Sandbox** (Ankit Bhargava part 2) — clearest practical explanation of `x-ms-user-isolation-key` and `x-ms-chat-isolation-key`, including session-resume behaviour and idle/cold-start semantics.
  https://ankitbko.github.io/blog/2026/05/hosted-agents-part-2/

- **Hosted agents in Foundry Agent Service (preview)** — canonical conceptual overview including session lifetime, idle behaviour, and protocol details.
  https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents

- **Introducing the new hosted agents in Foundry Agent Service** — announcement blog with per-session microVM, scale-to-zero, and OBO identity-passthrough details.
  https://devblogs.microsoft.com/foundry/introducing-the-new-hosted-agents-in-foundry-agent-service-secure-scalable-compute-built-for-agents/

---

## 2. Developing Foundry hosted agents — VS Code, azd, and the SDKs

The actual build environment.

- **Quickstart: Deploy your first hosted agent** — walks through the VS Code extension and `azd` workflow end-to-end.
  https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/quickstart-hosted-agent

- **Deploy a hosted agent** — deployment reference: container image, ACR push, version registration, polling for active status. Our `agent/scripts/deploy.py` follows this contract.
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent

- **Manage hosted agents** — managing versions, traffic routing, logs (`logstream` SSE endpoint), and the `azd ai agent monitor` command.
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/manage-hosted-agent

- **Microsoft Foundry Hosted Agents — What, Why, Protocols & Your First Deployment** (Ankit Bhargava part 1) — practitioner-oriented intro. Covers `FOUNDRY_*` environment variables and the Responses vs Invocations protocol choice (we use Responses).
  https://ankitbko.github.io/blog/2026/05/hosted-agents-part-1/

---

## 3. Microsoft Agent Framework — the host runtime

The host agent runtime for this project is Microsoft Agent Framework (`agent-framework-core` + `agent-framework-foundry` + `agent-framework-foundry-hosting`), running on a Foundry `gpt-5.x` deployment authenticated by the Foundry-assigned Managed Identity. The Responses host server (`agent-framework-foundry-hosting.ResponsesHostServer`, which depends on `azure-ai-agentserver-responses`) owns `/responses`; MAF's `AgentSession` (persisted to `$HOME`) owns the conversational thread; skills are loaded at boot from `agent/skills/*/SKILL.md`; and the Foundry Toolbox is consumed as a raw `MCPStreamableHTTPTool`. See [AGENTS.md §3 invariant 12](../AGENTS.md#3-non-negotiable-architectural-invariants) and [§4](../AGENTS.md#4-technology-choices-locked).

- **Foundry Hosted Agents (Agent Framework docs)** — `ResponsesHostServer` and `InvocationsHostServer` wrappers, Python and .NET examples.
  https://learn.microsoft.com/en-us/agent-framework/hosting/foundry-hosted-agent

- **Agent Chat History and Memory** — `AgentSession` serialisation/deserialisation, in-service vs in-memory storage of chat history. This is the mechanism we use to persist the host runtime's thread inside the Foundry per-session microVM `$HOME`.
  https://learn.microsoft.com/en-us/agent-framework/user-guide/agents/agent-memory

- **Step 4: Memory & Persistence** — practical session-state and context-provider patterns.
  https://learn.microsoft.com/en-us/agent-framework/get-started/memory

- **`MCPTool` (Agent Framework MCP client)** — the native MCP client we use to talk to the Foundry Toolbox. Replaces the hand-rolled `McpBridge` in the portal-generated `architecture/samplecode_toolbox.py`. Handles `initialize` / `mcp-session-id` / `notifications/initialized` / `tools/list` / `tools/call` / approval-item plumbing. Per-call header injection (used to stamp the Toolbox-channel bearer + `Foundry-Features` header — see [AGENTS.md §4.1](../AGENTS.md#41-foundry-toolbox-via-native-maf-mcpstreamablehttptool)) is first-class.
  https://learn.microsoft.com/en-us/agent-framework/user-guide/agents/tools/mcp

- **Getting Started with Foundry Hosted Agents** (Valentina Alto, Medium) — walk-through of going from a local agent to a deployed hosted one, with the same endpoint serving both portal and custom UIs.
  https://valentinaalto.medium.com/getting-started-with-foundry-hosted-agents-394959230136

---

## 4. Identity, OBO, and agent permissions

The auth model in one sentence: the desktop client attaches the user's bearer to `/responses` (Azure CLI public client, scope `https://ai.azure.com/.default`); the Foundry platform's **OAuth Identity Passthrough** exchanges that identity into WorkIQ tokens internally per Toolbox connection. The agent process holds no user tokens. See [AGENTS.md invariant 3](../AGENTS.md#3-non-negotiable-architectural-invariants).

- **Agent identity concepts in Microsoft Foundry** — Entra Agent ID, OAuth token exchange flow, blueprints, federated credentials.
  https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-identity

- **Conditional Access for Agent Identities in Microsoft Entra** — how Conditional Access applies to agent identities (Managed Identity) and to the user's interactive sign-in. Worth reading before configuring tenant CA policies for the agent's sign-in flow.
  https://learn.microsoft.com/en-entra/identity/conditional-access/agent-id

- **User Identity Passthrough for Hosted Agents Calling a Custom MCP Server** (Microsoft Q&A) — nuance: code-first hosted agents have different identity-passthrough behaviour than portal-created prompt agents. Useful if implementation hits edge cases.
  https://learn.microsoft.com/en-au/answers/questions/5872669/user-identity-passthrough-for-hosted-agents-callin

---

## 5. WorkIQ MCP servers

- **Work IQ MCP overview (preview)** — canonical overview, including licensing requirement and tenant enablement script.
  https://learn.microsoft.com/en-us/microsoft-agent-365/tooling-servers-overview

- **Work IQ API quickstart (preview)** — public-client and confidential-client setup paths. (Foundry-hosted agents using portal-configured Toolbox connections skip the custom app-registration steps — those scopes are pre-granted at the platform layer.)
  https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq-api-quickstart

- **Microsoft Work IQ API (preview)** — API reference, including the explicit statement that Work IQ uses delegated auth only and application-only is not supported.
  https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq-api-overview

- **Work IQ MCP overview (Copilot Studio docs)** — same overview from the Copilot Studio angle, including admin governance details.
  https://learn.microsoft.com/en-us/microsoft-copilot-studio/use-work-iq

- **microsoft/work-iq-samples** (GitHub) — canonical sample repo with tenant admin setup scripts and end-to-end .NET samples.
  https://github.com/microsoft/work-iq-samples

- **microsoft/work-iq-mcp User Guide** (DeepWiki) — covers the delegated-permissions-only constraint clearly.
  https://deepwiki.com/microsoft/work-iq-mcp/2-user-guide

---

## 6. Foundry Toolboxes (MCP-compatible tool bundling)

The path we take: build a single Foundry **Toolbox** in the project portal (named `Charter-Agent-Tools`) that bundles all WorkIQ MCP servers (Mail, Calendar, Files, Teams, User, Copilot, plus `workiq.ask`) behind one MCP-compatible endpoint, then point the hosted agent at that one endpoint via a raw MAF `MCPStreamableHTTPTool`. The Toolbox centrally handles credential injection, token refresh, and policy enforcement for everything inside it; the agent authenticates only to the Toolbox endpoint (Toolbox-channel bearer is the Foundry-assigned Managed Identity), and per-user identity is propagated into each MCP call by the Foundry platform's OAuth Identity Passthrough.

- **Connect agents to Model Context Protocol servers** (Microsoft Learn) — canonical reference. Contains the dedicated section **"Use Foundry Toolboxes as MCP endpoints"** with the endpoint URL format.
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/model-context-protocol

- **Create and use a Foundry Toolbox** (Microsoft Learn) — Toolbox setup guide. Covers prerequisites, creating a Toolbox, adding tools (Web Search, Code Interpreter, File Search, Azure AI Search, MCP servers, OpenAPI tools, A2A), versioning, and promoting a version to default.
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/toolbox

- **MCP server authentication for Foundry agents** (Microsoft Learn) — covers key-based, Microsoft Entra, and OAuth identity passthrough. Confirms that for a Toolbox, the agent uses `DefaultAzureCredential` against the Toolbox endpoint and the Toolbox manages per-tool credentials.
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/mcp-authentication

- **MCP tool REST reference (`OpenAIMCPTool`)** (Microsoft Learn) — wire-level shape for the `mcp` tool declaration (`server_url`, `server_label`, `allowed_tools`, `require_approval`, `project_connection_id`).
  https://learn.microsoft.com/en-us/azure/foundry/reference/foundry-project-rest-preview#openaimcptool

### Quick-reference: Toolbox endpoint URL format

- **Consumer endpoint** (production — always serves the promoted default Toolbox version):
  `{project_endpoint}/toolboxes/{toolbox_name}/mcp?api-version=v1`
- **Version-specific endpoint** (dev/test — validate a new Toolbox version before promotion):
  `{project_endpoint}/toolboxes/{toolbox_name}/versions/{version}/mcp?api-version=v1`

In code, the agent attaches the Toolbox as a raw `MCPStreamableHTTPTool` from `agent_framework._mcp`:

```python
from agent_framework._mcp import MCPStreamableHTTPTool

toolbox = MCPStreamableHTTPTool(
    name="workiq",
    url=f"{project_endpoint}/toolboxes/{toolbox_name}/versions/{toolbox_version}/mcp?api-version=v1",
    header_provider=_stamp_headers,
    http_client=_auth_injecting_client,   # stamps Bearer + Foundry-Features on every request
    approval_mode="never_require",
    request_timeout=90,
    load_prompts=False,                   # Foundry Toolbox does not implement prompts/list
)
```

Full wire-shape contract (header injection via `httpx` event hooks, why `header_provider` alone is insufficient, the `load_prompts=False` requirement, the 100-second non-streaming timeout, the `Foundry-Features: Toolboxes=V1Preview` header) is in [AGENTS.md §4.1](../AGENTS.md#41-foundry-toolbox-via-native-maf-mcpstreamablehttptool).

### Approval policy

WorkIQ **read** and **write** tools are both configured `approval_mode="never_require"` at the MCP layer — the capture loop and the kickoff fan-out would create unworkable UX otherwise. **Human approval for outbound side-effects is enforced at the skill layer**: the `sow-response` skill body refuses to call write tools without explicit user OK in the prompt, and the SOW Owner approves each drafted action turn-by-turn ([AGENTS.md §1.5](../AGENTS.md#15-current-implementation-status-may-21-2026), [invariant 5](../AGENTS.md#3-non-negotiable-architectural-invariants)). If an `actions/` Python module is ever extracted, it would carry a structured `SuggestedAction` lifecycle that enforces the same property in code.

### Known limitation

Non-streaming MCP tool call timeout of **100 seconds**. The capture loop must fan out channel-handler polls concurrently (`asyncio.gather`) rather than serially, and any single WorkIQ call must complete well under that bound. If a `workiq.ask` natural-language query starts hitting the limit, decompose it.

### Local working sample

A portal-generated reference sample lives at [`../architecture/samplecode_toolbox.py`](../architecture/samplecode_toolbox.py). It pins down the version-specific endpoint URL, the `DefaultAzureCredential` + `https://ai.azure.com/.default` token scope, the required `Foundry-Features: Toolboxes=V1Preview` header, and the MCP `initialize` / `mcp-session-id` / `tools/list` / `tools/call` flow. **It is reference-only** — its hand-rolled `McpBridge` is exactly what the native `MCPStreamableHTTPTool` replaces; no production code imports it.

**Standing rule.** The sample is a starting reference, not a contract. For anything beyond the calls shown in it — a new tool, a changed schema, a new MCP method, a bumped `protocolVersion`, a renamed header — introspect the **live Toolbox MCP endpoint** (`tools/list`, `initialize` capabilities) at runtime rather than coding against a stale snapshot. Treat Microsoft Learn (the links above) as the next-most-authoritative source after the live server.

---

## 7. Agent Skills format — the open agentskills.io specification

The skills under `agent/skills/` conform to the open **[agentskills.io](https://agentskills.io/specification)** specification (Anthropic-originated, now adopted by GitHub Copilot, VS Code, Claude Code, Goose, Gemini CLI, Kiro, fast-agent, Letta, Mistral Vibe). This is invariant 1 of the project ([AGENTS.md §3](../AGENTS.md#3-non-negotiable-architectural-invariants)) and is contractually load-bearing: every `agent/skills/{name}/SKILL.md` must be a valid Agent Skill per the spec.

A small in-repo loader (`runtime/skill_loader.py`, ~50 lines) reads each `agent/skills/*/SKILL.md`, validates the frontmatter against the agentskills.io shape, and injects the body into the host `Agent`'s instructions / tool-selection surface at boot. Conformance to the open spec buys us portability — the same `SKILL.md` body can be loaded by any agentskills.io client for isolated authoring/testing without standing up the full Foundry agent.

- **Specification** — directory layout, required YAML frontmatter fields (`name`, `description`), optional fields (`license`, `compatibility`, `metadata`, `allowed-tools`), naming rules (lowercase a–z / digits / hyphens, ≤64 chars, must match parent directory), description rules (≤1024 chars, must convey *what* and *when*), progressive disclosure stages (Discovery → Activation → Execution), and the optional `scripts/`, `references/`, `assets/` subdirs.
  https://agentskills.io/specification

- **Client showcase** — list of clients that load agentskills.io-compatible skills. A skill authored in this repo can be tested in Claude Code, VS Code Copilot, Goose, or Gemini CLI without modification.
  https://agentskills.io/clients

- **`skills-ref` validator** — the upstream CLI that validates a skill directory against the spec. Wire `skills-ref validate ./agent/skills/*` into CI on every PR that touches the skills directory.
  https://github.com/agentskills/agentskills/tree/main/skills-ref

What this gives us in practice (and what AGENTS.md §4.3 codifies as our local conventions on top):

- **Portability** — the same `SKILL.md` body can be loaded by any agentskills.io client.
- **Auditability** — skills are versioned files reviewed in PRs, not opaque prompt strings buried in code.
- **A sharp core/skill split** — see [AGENTS.md §4.4](../AGENTS.md#44-core-code-vs-skill--the-decision-rule). Deterministic plumbing (state I/O, cursors, idempotency, transport) stays as code; reasoning/generation/judgement is always a skill.

**Out of scope, deliberately:** Claude-Code-specific extension mechanisms — `plugins/`, per-skill `mcp.json`, slash commands — are *not* part of the agentskills.io spec and *not* used here. Tool discovery for this project is the **Foundry Toolbox over MCP** (§6 above), which replaces per-skill `mcp.json` files with one bundled MCP endpoint (`Charter-Agent-Tools`) consumed via the host runtime's native MAF `MCPStreamableHTTPTool`.

---

## Recommended reading order

If the implementing team is new to this stack:

1. **Hosted agents conceptual overview** (Microsoft Learn) — platform model first.
2. **Ankit Bhargava part 1** — practitioner's view, especially the protocol choice (we use Responses).
3. **Quickstart** — actually deploy a trivial hosted agent end-to-end. Half a day, no more.
4. **Ankit Bhargava part 2** — once something's deployed, this makes the isolation-keys story land.
5. **agentskills.io specification (§7 above)** — read this before writing or reviewing any `SKILL.md`; it is invariant 1.
6. **Agent Framework hosting integration** — once writing real agent logic.
7. **Work IQ MCP overview** plus verification that the tenant's existing portal agents can call WorkIQ — confirm dependencies are sorted before going further.
8. **Connect agents to MCP servers** + **Create and use a Foundry Toolbox** (§6 above) — read these before wiring WorkIQ into the agent, since the chosen integration pattern is one Foundry Toolbox endpoint that bundles all WorkIQ MCP servers, not direct per-server MCP connections.

A small caveat: several of these sources are recent enough (particularly the May 2026 Foundry hosted agents refresh) that official Learn docs and practitioner blogs are still settling into agreement. Where they disagree, trust Microsoft Learn for capability claims and the practitioner blogs for usage patterns.
