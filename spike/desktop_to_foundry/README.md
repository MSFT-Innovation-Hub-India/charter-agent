# Desktop → Foundry hosted agent spike

**What this spike is.** A throwaway desktop script that proves the end-to-end
client → hosted-agent → WorkIQ path the production design depends on:

1. A desktop client signs the user in interactively (Azure CLI public client
   for the spike; a shipping desktop app would use its own public-client app
   reg).
2. It calls the deployed Foundry hosted agent's `/responses` endpoint with
   the user's bearer token.
3. The agent's model decides to invoke a WorkIQ MCP tool (Calendar in this
   spike).
4. The Foundry runtime performs **per-user OAuth Identity Passthrough** to
   WorkIQ on the Toolbox connection and returns the calling user's actual
   data — **not** the agent's managed identity's data.

The first call per (user, connection) pauses the response stream with an
`oauth_consent_request` event. The script prints the consent URL; the user
opens it in a browser, grants consent, and the script resumes with
`previous_response_id`.

## What this spike established

- ✅ **Platform identity passthrough works.** A user bearer attached to
  `/responses` propagates through Foundry → Toolbox → WorkIQ MCP. The
  returned calendar belonged to the calling user, confirming the agent's
  managed identity is *not* substituted at the MCP layer.
- ✅ **The Azure CLI public client (`04b07795-…`) is sufficient for the
  desktop case.** It is pre-consented in every tenant and avoids any
  custom app-registration / admin-consent ceremony for the client side. A
  shipping desktop app would use its own public-client app reg, but the
  authentication surface remains "user signs in interactively, no admin
  involvement."
- ✅ **`oauth_consent_request` is the consent-pause contract.** The event
  carries a Microsoft login URL; the client opens it, the user consents to
  the specific WorkIQ connection once, and re-issuing the request with
  `previous_response_id` lets the agent pick up where it left off and call
  the tool.
- ✅ **`previous_response_id` resumes a turn cleanly after consent** — no
  need to rebuild conversation state on the client side.

## Bearing on the production architecture

The spike establishes the client model: the desktop client acquires the
user's token locally (MSAL public client or Windows broker) and POSTs it
to `/responses`. The agent process holds no WorkIQ tokens; identity
propagation past `/responses` is the Foundry platform's responsibility
via OAuth Identity Passthrough on the Toolbox connections.

The agent code does not care what kind of client is in front of it —
anything that can attach a user bearer to an HTTPS POST and stream SSE
works. Other clients in the broader sample family (cf. sibling sample
`workiq-agent-remote-client`, which uses Windows-identity + Teams bot +
Redis relay) use the same agent contract.

## What this is NOT

- Not a production client. Throwaway script.
- Not the rich desktop client architecture. That comes later.
- Not a test of the agent's reasoning quality. We're testing the auth + MCP
  dispatch wiring only.

## Prerequisites

1. **Agent deployed to Foundry hosted agents.** This spike is moot if the
   agent is running locally — Foundry's MCP passthrough only engages when the
   agent runs inside Foundry's hosting and the user identity arrives through
   the platform's request validation.
2. **Your user account has `Azure AI User` role** on the Foundry project
   `ocvp-agent-svc` (required for any user-bearer call into a hosted agent).
3. **The `Charter-Agent-Tools` Toolbox connections are configured for
   Identity Passthrough** in the Foundry portal (not Stored Credentials).
4. Python 3.12+ with `msal` and `httpx`:

   ```pwsh
   pip install msal httpx
   ```

## Run

```pwsh
$env:AGENT_RESPONSES_URL = "https://ocvp-agent-svc-resource.services.ai.azure.com/api/projects/ocvp-agent-svc/agents/charter-agent/endpoint/protocols/openai/responses?api-version=v1"
# Optional — defaults are the Azure CLI public client + common tenant
# $env:SPIKE_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
# $env:SPIKE_TENANT_ID = "common"
python calendar_today.py
```

The script will:

1. Pop a device-code prompt (paste into https://aka.ms/devicelogin in any browser).
2. Acquire a token for `https://ai.azure.com/.default`.
3. POST a single message ("What does my day look like in the calendar today?")
   to `/responses` and stream the SSE response.
4. On `oauth_consent_request`, print the URL, wait for you to consent, then
   retry the request (with `previous_response_id` so the agent picks up where
   it left off).
5. Print the final `response.completed` text.

## Auth client ID

By default this uses the **Azure CLI public client** (`04b07795-...`), which
is pre-consented in most tenants and works without any app registration.
That's the right choice for a spike. The real desktop client will use its own
public-client app reg.

## Interpreting results

- ✅ **Success:** the model calls a tool like `workiq___calendar___list_events`
  (or similar — the actual tool surface is Foundry-managed) and the
  `response.completed` event contains a summary of *your* calendar.
- 🛑 **`oauth_consent_request` never resolves:** the WorkIQ connection in the
  Toolbox is not configured for Identity Passthrough, or the scope it requests
  is admin-only in your tenant.
- 🛑 **401/403 at the `/responses` call itself:** your account doesn't have
  `Azure AI User` on the Foundry project.
- 🛑 **Tool call succeeds but returns the agent's MI's calendar:** the MCP
  call isn't going through the Toolbox identity-passthrough path. Re-check
  `runtime/foundry_host.py` and confirm the Toolbox connection's auth mode
  is *Identity Passthrough* (not *Stored Credentials*) in the Foundry portal.
