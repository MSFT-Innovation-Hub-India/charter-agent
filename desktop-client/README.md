# Project Charter Desktop Agent

A standalone pywebview rich client for the `charter-agent`. Has its own venv so you can iterate on the client independently of the agent.

## Layout

```
desktop-client/
├── app.py            Python entry: auth, SSE streaming, JS bridge
├── ui.html           Single-file UI (dashboard widget + chat + input)
├── requirements.txt  pywebview, httpx, azure-identity
└── README.md
```

## What's in the window

- **Header** — app title, current `agent_session_id`, current endpoint, auth pill, *Run against* dropdown (Hosted / Local), *Sign in*, *New project*.
- **Dashboard widget** at the top — empty placeholder until the agent emits a ```` ```json {"kind":"dashboard", …} ``` ```` snapshot, then renders project name, status pill, progress bar, owner tiles, exceptions panel. (The *Refresh status* quick-action prompts the agent for exactly that shape.)
- **Quick actions** — *Kickoff SOW (Northwind)*, *Refresh status*, *Check submissions*, *Show charter*. Same surface a future direct-action button bar can sit on.
- **Transcript** — user / agent / tool / system / error bubbles with live SSE streaming, scrollable, persists for the window's lifetime.
- **Input bar** — textarea + Send. Enter sends; Shift+Enter inserts a newline.

## Endpoints

The client speaks the OpenAI Responses protocol against one of two endpoints, switchable at runtime via the dropdown in the header:

| Mode     | Default URL                            | When to use                                                                      |
|----------|----------------------------------------|----------------------------------------------------------------------------------|
| `local`  | `http://localhost:8088/responses`      | A locally-running charter-agent.                                                 |
| `hosted` | from `AGENT_ENDPOINT_HOSTED`           | The deployed Foundry-hosted agent.                                               |

Set whichever endpoints you'll use; the dropdown disables unconfigured modes. Switching modes drops the current session id so the next message lands in the right sandbox.

## Setup

```powershell
cd c:\Users\sansri\WorkIQ-Sample-Agents\charter-agent\desktop-client

# isolated venv (so the agent and the client can drift independently)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
.\.venv\Scripts\Activate.ps1

# pick one or both
$env:AGENT_ENDPOINT_LOCAL  = "http://localhost:8088/responses"
$env:AGENT_ENDPOINT_HOSTED = "https://<your-deployed-agent>/responses"

# (optional) initial mode; default is hosted, falls back to local if hosted unset
$env:AGENT_ENDPOINT_MODE = "hosted"   # or "local"

# (optional) bind to an existing sandbox so the first turn lands in that $HOME
# $env:AGENT_PROJECT_ID = "<agent-session-id>"

az login
python app.py
```

CLI flags override env vars:

```powershell
python app.py --mode local --local-url http://localhost:8088/responses
python app.py --mode hosted --hosted-url https://<deployed-agent>/responses
```

## Testing the agent separately

In another terminal, boot the local agent (its own venv lives at `..\agent\.venv`):

```powershell
cd c:\Users\sansri\WorkIQ-Sample-Agents\charter-agent\agent
.\.venv\Scripts\Activate.ps1
# whatever your local-run entrypoint is — e.g. dev_run.py, or directly:
python -m charter_agent
```

Then in the client window pick **Local agent** from the dropdown. Flip to **Hosted (Foundry)** any time to compare behaviour against the deployed agent — switching drops the session id so each side starts from a clean sandbox.

## Heads up

- **Kickoff is destructive.** *Kickoff SOW (Northwind)* triggers the §6 fan-out in `..\agent\skills\sow-response\SKILL.md` — real Teams DMs to internal collaborators and real emails to external addresses from the bundled fixture. Only click against a tenant where those side effects are OK.
- **Mock-data note.** The bundled scenario assumes the kick-off meeting *notes* arrive as an email (no actual Teams meeting took place). The skill is updated to accept any of: meeting transcript, email body/attachment, Teams chat thread, or linked SharePoint/OneDrive doc — whichever WorkIQ Copilot surfaces.
- **Consent flow.** First-time access to a WorkIQ connection emits `oauth_consent_required`; the client opens the consent URL in a browser, waits ~8 seconds, and retries with `previous_response_id`. If consent takes longer, re-send the same prompt.
- **Dashboard JSON contract.** The widget renders the first ```` ```json {"kind":"dashboard", …} ``` ```` block in the agent's reply. A future iteration can lift this to a typed MAF tool return value.
