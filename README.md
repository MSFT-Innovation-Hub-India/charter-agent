# charter-agent

A Microsoft Foundry **hosted agent** that turns a natural-language project
brief into a ratified Project Charter, fans out the workstreams across
Microsoft 365 (SharePoint, Teams, Outlook, email) via the **WorkIQ MCP
servers**, watches the channels for deliveries, infers status, drafts
nudges/reassignments for human approval, and consolidates the final
artifact. The workspace dissolves when the project closes.

> **This is a sample / reference implementation**, not a shipping product.
> It exists alongside other agents in the `WorkIQ-Sample-Agents` family and
> is the canonical example of a **Foundry hosted agent that calls WorkIQ on
> behalf of the calling end user** via the platform's OAuth Identity
> Passthrough mechanism.

## Where to read next

The repository is documented in layers — start at the top and descend only
as far as the change you're making requires.

| Document | Purpose | When to read |
|---|---|---|
| [`AGENTS.md`](AGENTS.md) | **Operating contract** for every coding agent (and human) working in this repo. Non-negotiable invariants, tech choices, layout, conventions, change-safety checklist. Note: §3 invariant 3 + every "SOW-Owner OBO" / `workiq_token.py` / `COORDINATOR_OBO_*` mention is **superseded** by the identity-passthrough finding (see below). | Before every non-trivial change. |
| [`functional-specs/project_workspace_spec.md`](functional-specs/project_workspace_spec.md) | What & why. Requirements, scenarios, channel taxonomy, status semantics, phase plan. | When changing scope or behaviour. |
| [`architecture/architecture_and_design.md`](architecture/architecture_and_design.md) | How. Components, contracts, sequences, schemas, module seams. The top banner block flags which sections are stale relative to the May 2026 cleanup. | When implementing. |
| [`functional-specs/references.md`](functional-specs/references.md) | External docs the design is grounded in. | When the platform behaviour surprises you. |
| [`spike/desktop_to_foundry/README.md`](spike/desktop_to_foundry/README.md) | The throwaway spike that proved the identity-passthrough auth path end-to-end. | When wiring a new client surface or revisiting auth. |
| [`agent/skills/sow-response/SKILL.md`](agent/skills/sow-response/SKILL.md) | The single skill the agent runs today. Drives the whole workflow. | When changing agent behaviour. |

## Current state (May 2026)

- **Agent deployed and verified** on the dev Foundry project `ocvp-agent-svc`
  (model deployment `gpt-5.4`). A calendar query through `/responses`
  returns real Outlook data for the calling user.
- **One skill** ships today: [`sow-response`](agent/skills/sow-response/),
  per [`AGENTS.md` §1.5](AGENTS.md). The eight-skill split described
  elsewhere in the contract is a possible evolution, not the current code.
- **23 tests pass** under `agent/tests/`.
- **Client surface**: the deployed agent is exercised end-to-end by the
  desktop spike under [`spike/desktop_to_foundry/`](spike/desktop_to_foundry/).

## Authentication & identity model (post-spike)

This is the single most important architectural decision in the codebase
and the one most at odds with the older sections of `AGENTS.md` and
`architecture/architecture_and_design.md`. **Read this before touching
anything that talks to WorkIQ.**

### What works

**OAuth Identity Passthrough at the Foundry MCP connection layer.** The
desktop client authenticates the *end user*, attaches their bearer to
`/responses`, and the Foundry runtime exchanges that identity into a
WorkIQ token internally, per connection. The agent process holds no
WorkIQ refresh tokens, signs no JWTs, and runs no OBO flow.

Flow:

1. Client signs the user in (MSAL public client or `az login`).
2. Client POSTs to `/responses` with `Authorization: Bearer <user_token>`
   (scope `https://ai.azure.com/.default`).
3. On the first call per (user, WorkIQ connection), the response stream
   emits an `oauth_consent_request` SSE event with a Microsoft login URL.
   Client opens it in a browser; user consents once.
4. Client retries with `previous_response_id` set; the agent resumes the
   same turn, the MCP call now succeeds in the user's context, and the
   response contains the calling user's actual M365 data.

### What was tried and shelved

A custom confidential-client app registration + admin-consented delegated
scopes on the WorkIQ resource apps + server-side OBO exchange (the
`SOW_OWNER_OBO_*` / `workiq_token.py` machinery still referenced in stale
sections of the architecture doc). This dead-ended in microsoft.com for
two independent reasons:

1. **`sansri@microsoft.com` cannot admin-consent** in the microsoft.com
   tenant (`Forbidden / RequestDenied`). Any architecture requiring a
   fresh admin-consent step is blocked.
2. **The WorkIQ resource apps in the tenant are *client* apps, not
   resource APIs** — they expose zero `oauth2PermissionScopes` and have
   no `api://` SPN. Even with admin rights there is nothing to request
   delegated permissions against.

The dead modules (`runtime/workiq_token.py`,
`runtime/workiq_token_cache.py`, `scripts/bootstrap_workiq_token.py`,
`scripts/setup_obo_app_reg.ps1`) and their env vars
(`SOW_OWNER_OBO_TENANT_ID/CLIENT_ID/CLIENT_SECRET`, `WORKIQ_SCOPE`) were
removed in the May 22 cleanup.

### Client surface

The only client is the desktop spike under [`spike/desktop_to_foundry/`](spike/desktop_to_foundry/). It signs the user in via the Azure CLI public client (or any pre-consented public client) and POSTs the user bearer to `/responses`. The agent code itself does not care what kind of client is in front of it — anything that can attach a user bearer to an HTTPS POST and stream SSE works.

## Repository layout

See [`AGENTS.md` §5](AGENTS.md#5-repository-layout-target). Briefly:

- [`agent/`](agent/) — the hosted agent (Python 3.13, MAF + Foundry).
- [`functional-specs/`](functional-specs/) — requirements.
- [`architecture/`](architecture/) — design.
- [`spike/`](spike/) — throwaway proofs (currently: desktop → Foundry
  passthrough; the desktop spike is also the sample's only client).
- [`test-fixtures/`](test-fixtures/) — one sample project for end-to-end
  runs; **not** a normative scenario.

## Running, deploying, and testing

There are three workflows. The deployed agent on Foundry is the source of
truth — local boots are for de-risking import-time, MCP wiring, and skill
loading before pushing an image.

### 1. Local development

Boots the agent in this repo against the **real** Foundry project, using
your `az login` identity in place of the production Managed Identity. The
host model and the WorkIQ Toolbox are remote; only the Python process is
local. Note: the Foundry **MCP Identity Passthrough only engages when the
agent runs inside Foundry hosting** — locally, every WorkIQ call runs as
the developer's `az login` identity. Local boots are therefore for plumbing
work (skill loading, Toolbox enumeration, prompt iteration), not for
validating per-user identity propagation.

```pwsh
# one-time, from repo root
cd agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

# auth + config
az login --tenant <your-tenant>
Copy-Item .env.example .env   # edit if you need to repoint deployment/project
```

Then either run a focused command or boot the full Responses server.

**Focused probes** (no HTTP server, fast feedback):

```pwsh
python scripts/dev_run.py skills        # what skill_loader picks up
python scripts/dev_run.py list-tools    # what the Toolbox enumerates
python scripts/smoke_calendar.py        # one calendar query end-to-end via the host model + Toolbox
```

**Full Responses server** (mirrors what Foundry runs in production):

```pwsh
# terminal A
python -m charter_agent
# -> listens on http://localhost:8088/responses

# terminal B
python scripts/smoke_responses.py "list your skills"
python scripts/smoke_responses.py "..." --previous-response-id <id-from-prior-response>
```

**Tests**:

```pwsh
python -m pytest -q     # 23 tests, all pure-Python (no network)
```

### 2. Deploy to Foundry hosted agents

Two-step: build & push the image to ACR, then register a new agent
version against the Foundry project. The deploy doesn't run `docker
build` for you — `azd deploy` or a manual ACR build does.

**Build & push** (the image tag in `agent/scripts/deploy.py` defaults to
`pcdotaiagentd10b5a.azurecr.io/charter-agent:v1` — override
`AGENT_IMAGE` to bump):

```pwsh
# from repo root; ACR remote build (handles linux/amd64 even on ARM)
az acr build `
  --registry pcdotaiagentd10b5a `
  --image charter-agent:v2 `
  --file agent/Dockerfile `
  agent/
```

> Foundry hosted agents reject non-`linux/amd64` images. On Apple Silicon
> or Windows ARM, always use `az acr build` (remote) or
> `docker buildx build --platform=linux/amd64 …`.

**Register the new version**:

```pwsh
cd agent
$env:AGENT_IMAGE = "pcdotaiagentd10b5a.azurecr.io/charter-agent:v2"
python scripts/deploy.py
```

`deploy.py` calls `project.agents.create_version(...)` with a
`HostedAgentDefinition` (CPU 0.5, memory 1Gi, Responses protocol 1.0.0,
env vars from `AZURE_AI_MODEL_DEPLOYMENT_NAME` + `TOOLBOX_NAME`), polls
until `status=active` (~1–3 min), and prints the gated Responses URL of
the form:

```
https://<project>.services.ai.azure.com/api/projects/<project>/agents/charter-agent/endpoint/protocols/openai/responses?api-version=v1
```

The Foundry-injected env on the running container additionally includes
`FOUNDRY_PROJECT_ENDPOINT` and `APPLICATIONINSIGHTS_CONNECTION_STRING` —
do not set those in `agent.yaml` or via `deploy.py`.

### 3. Testing the deployed agent end-to-end

Two complementary surfaces:

**A. Calendar-on-behalf-of-user spike** ([`spike/desktop_to_foundry/`](spike/desktop_to_foundry/))
— the proven path for validating that the WorkIQ call returns the
calling user's data and not the agent's MI. This is the only place where
the `oauth_consent_request` → consent-in-browser →
`previous_response_id`-resume cycle is exercised end-to-end.

```pwsh
cd spike/desktop_to_foundry
pip install msal httpx
$env:AGENT_RESPONSES_URL = "<gated Responses URL from deploy.py output>"
python calendar_today.py
```

First run pauses with a consent URL; subsequent runs against the same
user complete in one shot.

**B. Application Insights traces** — see the **Telemetry** section below.

## Telemetry

### What is emitted, by whom

| Source | Spans/events | Wired by |
|---|---|---|
| Foundry hosted-agent platform | Root `/responses` request span; child spans for each model call and each MCP `tools/list` / `tools/call`; GenAI semantic-conventions attributes (`gen_ai.system`, `gen_ai.request.model`, token counts) | Auto, once App Insights is connected to the Foundry project. No code in this repo wires it. |
| MAF + the Responses host server | Tool-dispatch internals, SSE event emission, structured logs | Auto. We hand the warm `Agent` to `ResponsesHostServer(agent).run()` and the rest is library-side. |
| This repo — `observability.ProcessAttributesSpanProcessor` | Stamps every span with `project.id` and `gen_ai.conversation.id` from `FOUNDRY_AGENT_SESSION_ID` so multi-project queries can filter cleanly | `_enable_tracing()` in [`__main__.py`](agent/src/charter_agent/__main__.py), once per process. |
| This repo — `@trace_function` decorator (re-exported from `azure.ai.projects.telemetry`) | Custom child spans wherever the agent code wraps a non-trivial operation | Decorator on the function. No `tracer.start_as_current_span(...)` by hand. |
| This repo — `observability.log_activity(...)` | Append-only NDJSON to `$HOME/activity.json`; each entry includes the current OTel `span_id` so audit lines correlate with traces | Explicit call wherever state mutates. This is **product behaviour** (the audit narrative the dashboard renders), not telemetry. |

The OTel exporter (Azure Monitor) is wired by the platform via the
auto-injected `APPLICATIONINSIGHTS_CONNECTION_STRING`. We do **not**
call `configure_azure_monitor(...)` ourselves, and we do **not** create
or own a `TracerProvider`.

### What we deliberately do NOT do

**`AIProjectInstrumentor().instrument()` is intentionally not called.**
The docstring for `_enable_tracing()` in [`__main__.py`](agent/src/charter_agent/__main__.py)
records why: `azure-ai-projects` 2.0.1 / 2.1.0 ships a Responses
instrumentor that wraps the upstream stream in an `AsyncStreamWrapper`
which lacks the `.headers` attribute that
`agent_framework_foundry`'s streaming consumer reads. Enabling it
crashes every Responses turn with
`'AsyncStreamWrapper' object has no attribute 'headers'`. Re-enable
once that upstream bug is fixed. As long as it's off, the
`AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING` env var is also a no-op for
us — leave it unset.

### Azure resources

| Resource | Name | RG | Purpose |
|---|---|---|---|
| App Insights | `app-insights-uc3lfx4p7wxjy` | `sre-rg` | Sink for all spans, traces, exceptions, and dependencies emitted by the agent container. Connection string auto-injected into the agent process by the Foundry platform — not set in `agent.yaml` or `deploy.py`. |
| Foundry project | `ocvp-agent-svc` | `pcdotai-agent` | Hosts the agent; owns the connection to App Insights above. |

The Foundry-to-App-Insights connection is configured once per project
in the portal (Foundry project → Tracing → Connect to Application
Insights). The agent picks it up on next deploy via the injected env.

### Querying

The KQL pattern that has been most useful for end-to-end debugging:

```pwsh
$env:PYTHONIOENCODING = "utf-8"
az monitor app-insights query `
  --app app-insights-uc3lfx4p7wxjy -g sre-rg `
  --analytics-query "union traces, exceptions, requests, dependencies
    | where timestamp > ago(15m)
    | where cloud_RoleName has 'charter-agent'
    | order by timestamp desc | take 80
    | project timestamp, itemType, severityLevel, message,
              outerMessage = tostring(customDimensions['outerMessage']),
              operation_Name, resultCode, success" `
  --subscription bc2e2415-164d-45a5-9a4a-29d9264a343e -o table
```

The `union` is deliberate — Foundry's spans land as `requests` and
`dependencies`, MAF and library logs land as `traces`, and any unhandled
errors land as `exceptions`. Filtering by `cloud_RoleName has 'charter-agent'`
isolates this agent from anything else in the same App Insights workspace.

For trace-local debugging, pivot off `operation_Id` (the trace ID) once
you have one row from the union above:

```kusto
union traces, requests, dependencies, exceptions
| where operation_Id == "<trace-id>"
| order by timestamp asc
```
