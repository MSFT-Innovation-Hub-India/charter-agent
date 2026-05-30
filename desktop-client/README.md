# Charter Agent — Desktop Client

A native Windows desktop application that provides the user-facing surface for the Charter Agent. Built with pywebview (Chromium-based WebView2 host), it authenticates the user, streams agent responses in real time, manages multiple concurrent projects, and renders a live project dashboard.

> **Navigation:** [Root README](../README.md) · [Agent backend](../agent/README.md) · [Architecture](../architecture/architecture_and_design.md)

---

## Contents

1. [What it is](#1-what-it-is)
2. [Setup and run](#2-setup-and-run)
3. [Authentication](#3-authentication)
4. [Calling the agent: local vs hosted](#4-calling-the-agent-local-vs-hosted)
5. [Project management and the sidebar](#5-project-management-and-the-sidebar)
6. [Local storage and cache](#6-local-storage-and-cache)
7. [SSE streaming and the agent event model](#7-sse-streaming-and-the-agent-event-model)
8. [Dashboard rendering](#8-dashboard-rendering)
9. [Connection and service status](#9-connection-and-service-status)
10. [The autonomous agent trigger](#10-the-autonomous-agent-trigger)
11. [Background poller and system tray](#11-background-poller-and-system-tray)
12. [Architecture: Bridge pattern](#12-architecture-bridge-pattern)

---

## 1. What it is

```
desktop-client/
├── app.py               Thin entry point: argparse, single-instance lock, window/bridge/poller/tray wiring
├── charter_client/      The client package — all logic lives here
│   ├── __init__.py
│   ├── config.py        Env loading (.env), path anchors, constants, logger, SDK/broker capability guards
│   ├── protocol.py      SSE parsing + event normalisation (raw-httpx and SDK events → one dict shape)
│   ├── auth.py          MSAL/WAM sign-in, JWT decode, AuthenticationRecord, _BridgeTokenCredential
│   ├── storage.py       projects.json, transcripts, view cache, disk-state readers
│   ├── notifications.py Windows toast helper
│   ├── poller.py        AutoPoller — opt-in background check scheduler
│   ├── bridge.py        The Bridge class: both transports (SDK + legacy raw-httpx) + the js_api surface
│   └── tray.py          Single-instance mutex + Win32 system-tray wiring
├── tray_icon.py         Win32 system-tray icon (ctypes — no third-party tray library)
├── ui.html              UI markup shell — links assets/app.css and assets/app.js
├── assets/
│   ├── app.css          The UI design system / stylesheet (extracted from ui.html)
│   ├── app.js           The UI SPA logic (extracted from ui.html)
│   ├── app_icon.png
│   └── app_icon.ico
├── scripts/
│   ├── start.ps1        Start the app in the background (no console window)
│   ├── stop.ps1         Stop the running instance
│   └── restart.ps1      Stop then start
├── .env                 Local configuration overrides (gitignored)
├── .env.example         Configuration template (safe to check in)
└── requirements.txt
```

The application runs as a pywebview window — a native Win32 window hosting a Chromium WebView2 pane. The Python code (the `charter_client` package) handles all networking and authentication; the front-end (`ui.html` + `assets/app.css` + `assets/app.js`) handles all rendering. They communicate over the pywebview `js_api` bridge: JavaScript calls Python methods on the `Bridge` instance directly, and Python pushes events to JavaScript via `window.evaluate_js()`.

`app.py` is intentionally thin — it parses CLI flags, takes the single-instance lock, builds the `Bridge`, starts the `AutoPoller`, creates the window, and hands off. Everything substantive lives in `charter_client/`. `config.py` runs its import side effects first (mute pywebview loggers, detect `azure-identity-broker` and the Foundry SDK, load `.env`, configure the logger), so importing it before any env-derived constant is read is load-bearing.

### UI assets are loaded over `file://`

pywebview points the WebView2 pane at `ui.html` via a `file://` URL. The `<link rel="stylesheet" href="assets/app.css">` and `<script src="assets/app.js">` references resolve relative to that location off disk — no bundler, no build step, no dev server. `app.js` is a classic (non-module) script on purpose: under `file://`, WebView2 applies CORS to `type="module"` loads and they can fail silently, so the SPA stays a single classic script.

Closing the window does **not** exit the process — the app hides to the system tray and continues running the background poller. Click the tray icon to restore the window. Use **Quit** from the tray right-click menu to fully exit.

---

## 2. Setup and run

### Prerequisites

- Windows 10/11, Python 3.12+
- WebView2 runtime (pre-installed on Windows 11; download from Microsoft for Windows 10)
- Active `az login` session (or use the in-app Sign in button for WAM-based auth)
- The Foundry hosted agent endpoint URL (from the Foundry portal)

### Setup (one-time)

```powershell
cd desktop-client
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Configuration (.env)

Copy `.env.example` to `.env` and edit. The file is gitignored — it holds your deployment-specific values. Shell environment variables always win over `.env`.

```powershell
Copy-Item .env.example .env
# Edit .env — at minimum set FOUNDRY_PROJECT_ENDPOINT or AGENT_ENDPOINT_HOSTED
```

Key settings in `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_ENDPOINT_HOSTED` | _(auto-derived)_ | Full `/responses` URL of the Foundry agent |
| `FOUNDRY_PROJECT_ENDPOINT` | — | Used to auto-build the hosted URL **and** as the SDK transport's project endpoint; if unset, the SDK path is unavailable and the client uses the raw-httpx transport |
| `AGENT_NAME` | `charter-agent` | Agent name used when constructing the hosted URL and when minting the SDK session (`create_session`) |
| `AGENT_VERSION` | _(latest)_ | Optional pinned agent version for the SDK session (`VersionRefIndicator`) |
| `CHARTER_CLIENT_TRANSPORT` | `sdk` | `sdk` (default) or `legacy` to force the raw-httpx transport even in hosted mode |
| `AGENT_ENDPOINT_MODE` | `hosted` | `hosted` or `local` |
| `AGENT_ENDPOINT_LOCAL` | `http://localhost:8088/responses` | Local dev server URL |
| `SPIKE_TENANT_ID` | _(discovered)_ | Entra tenant ID for MSAL token acquisition |
| `CHARTER_POLL_INTERVAL_MINS` | `30` | How often the background poller checks for new replies |
| `CHARTER_POLL_BIZ_HOURS_ONLY` | `0` | Set to `1` to restrict polling to Mon-Fri 07:00–20:00 local time |

### Run

**Option A — scripts (recommended, no console window):**

```powershell
# Start in background, icon appears in system tray
.\scripts\start.ps1

# Stop
.\scripts\stop.ps1

# Restart
.\scripts\restart.ps1

# Kill any existing instance and start fresh
.\scripts\start.ps1 -Force
```

**Option B — directly (with console, useful for debugging):**

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

### CLI flags

```powershell
python app.py --mode local
python app.py --mode hosted --hosted-url https://<agent>/responses
python app.py --debug        # opens WebView2 DevTools (right-click → Inspect)
```

---

## 3. Authentication

### How the user signs in

The client uses **Azure Identity** (`azure-identity` + optional `azure-identity-broker`) to authenticate the user and obtain a bearer token with scope `https://ai.azure.com/.default`.

Authentication falls through a priority chain:

1. **Windows Account Manager (WAM) broker** — if `azure-identity-broker` is installed, the Windows native account picker appears. Silent for accounts already signed in to Windows.
2. **Interactive browser** — fallback when the broker is unavailable. Opens the system browser for sign-in.
3. **Silent refresh** — if a previous session's `AuthenticationRecord` is saved to disk, the app attempts a silent token refresh at startup. The user sees "signed in" immediately without a popup.

### Token persistence

After the first successful interactive sign-in, the client saves an **`AuthenticationRecord`** to `%USERPROFILE%\.charter-agent\auth_records\charter-agent-desktop.json`. This record contains enough information to silently re-acquire tokens on subsequent launches — no password re-entry, no browser pop-up, unless the refresh token expires.

The MSAL token cache itself is encrypted on disk via Windows DPAPI (through `azure-identity`'s `TokenCachePersistenceOptions`).

### What the token is used for

The bearer token is attached to **every `/responses` POST** as `Authorization: Bearer <token>`:

```python
headers = {
    "Authorization": f"Bearer {self.token}",
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
}
```

The Foundry platform uses this token to:
1. **Validate the user identity** — confirm the caller is an authenticated tenant member
2. **Propagate the user's identity into WorkIQ tool calls** via OAuth Identity Passthrough (see [agent/README.md §7](../agent/README.md#7-foundry-toolbox--one-mcp-endpoint-for-all-of-m365))

The token is **refreshed automatically** before it expires. If `time.time() >= token_expires_at - 60`, a refresh is triggered at the start of every `send()` call, before the request is made.

### WorkIQ consent flow

On the first request that triggers a WorkIQ tool call, the Foundry platform emits an `oauth_consent_request` SSE event rather than the tool result. The client:

1. Detects the event and extracts the consent URL
2. Displays a system message in the transcript: "Opening consent URL — grant access in the browser that just opened"
3. Opens the URL in the default system browser via `webbrowser.open()`
4. Waits ~8 seconds (enough for most users to grant consent)
5. Retries the same prompt with `previous_response_id` set to the paused response's ID

After consent is granted once, all subsequent WorkIQ calls for that user and that connection succeed silently.

---

## 4. Calling the agent: local vs hosted

The client speaks the same **OpenAI Responses protocol** regardless of which endpoint it targets. Switching modes is purely a URL change.

| Mode | Default URL | Agent location |
|---|---|---|
| `local` | `http://localhost:8088/responses` | Python process on this machine |
| `hosted` | from `AGENT_ENDPOINT_HOSTED` env var | Deployed Foundry native agent |

### Two transports: SDK (hosted default) and raw-httpx (legacy fallback)

The client speaks the Responses protocol over **two interchangeable transports**. Both feed their events through the same switch in `Bridge` and share the same post-stream tail (`_finalize_turn`), so fork detection, the empty-completion retry, consent handling, and dashboard capture are identical regardless of which one runs.

| Transport | Method | When it runs | How it talks to Foundry |
|---|---|---|---|
| **SDK** (showcase / default) | `Bridge._post_one_sdk` | Hosted mode, when the Foundry SDK is importable **and** a project endpoint resolves | `azure-ai-projects` — `AIProjectClient(...).get_openai_client(agent_name=…).responses.create(stream=True, …)` |
| **Legacy raw-httpx** (fallback) | `Bridge._post_one_legacy` | Local mode, **or** any time the SDK path raises | Hand-rolled `httpx` streaming POST + a custom SSE parser (`protocol._iter_sse_events`) |

`Bridge._post_one` is the dispatcher. It picks the SDK path when **all** of these hold: `mode == "hosted"`, the SDK imported at boot (`config._SDK_AVAILABLE`), `CHARTER_CLIENT_TRANSPORT` is not `legacy`, and `_resolve_project_endpoint()` returns a non-empty Foundry project endpoint. Otherwise — or if the SDK call throws — it falls through to the raw-httpx transport. Set `CHARTER_CLIENT_TRANSPORT=legacy` in the environment to force the raw-httpx path even in hosted mode (useful for debugging the wire).

**Why two transports.** The SDK is the idiomatic, supported way to drive a Foundry-hosted agent: it mints the session server-side, owns the bearer/credential plumbing, and yields typed Responses events. But the SDK can only target a Foundry *project* endpoint — it cannot drive a `charter-agent` serving `/responses` on `http://localhost:8088` (localhost is not a project endpoint). The raw-httpx transport is therefore the only way to reach a **local** agent, and it doubles as the resilience fallback if the SDK path ever fails. It is retained verbatim and is **not** dead code.

#### SDK transport — how it connects

1. **Project client** — `_get_project_client()` lazily builds and caches one `AIProjectClient(endpoint=<project endpoint>, credential=_BridgeTokenCredential(self), allow_preview=True)`. The credential is the crucial part: `_BridgeTokenCredential` wraps the **signed-in end-user's MSAL bearer** (not `DefaultAzureCredential`), so Foundry OAuth Identity Passthrough still reaches WorkIQ as the user (invariant 3).
2. **OpenAI client** — `_get_openai_client()` calls `project_client.get_openai_client(agent_name=<AGENT_NAME or "charter-agent">)`, which returns an OpenAI-compatible Responses client bound to that agent.
3. **Session** — `_ensure_session_sdk()` mints the session **once per project** via `project_client.beta.agents.create_session(agent_name=…, isolation_key=<project_id>)` (plus an optional pinned `AGENT_VERSION`). The platform returns an `agent_session_id`, which is persisted to `projects.json` *immediately* — before the first `responses.create` — so a crash mid-turn never strands the project without its session handle.
4. **Turn** — `responses.create(input=<prompt>, stream=True, extra_body={"agent_session_id": <session_id>}, previous_response_id=<id-or-omitted>)`. The SDK streams typed events; `protocol._sdk_event_to_dict` normalises each one to the same dict shape the legacy SSE parser produces, and the shared switch handles it.

Note the **two distinct keys** in the SDK path: `isolation_key` is the stable, client-owned key (we use the `project_id`) that pins one Foundry session per project; `agent_session_id` is the platform's session handle that we persist and pass on every turn. They are kept separate and never conflated.

#### Legacy raw-httpx transport — how it connects

No SDK objects. `_post_one_legacy` builds the request by hand:

```python
body = {"input": prompt, "stream": True}
if session_id:           body["agent_session_id"] = session_id
if previous_response_id: body["previous_response_id"] = previous_response_id
headers = {
    "Authorization": f"Bearer {self.token}",      # the user's MSAL bearer
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
    "x-agent-chat-isolation-key": session_id,      # one Foundry session per project
}
```

Here the client **mints the session id itself** — it uses the `project_id` as the `agent_session_id` (mirrored into the `x-agent-chat-isolation-key` header) rather than calling `create_session`. It then streams the POST with `httpx.Client(...).stream(...)` and parses the SSE frames with `protocol._iter_sse_events`. The event handling from `response.created` onward is identical to the SDK path.

### What differs between modes

| Concern | Local mode | Hosted mode |
|---|---|---|
| Model | Remote — Foundry cloud (`gpt-5.x`) | Remote — same deployment |
| Toolbox / WorkIQ | Remote — Foundry cloud Toolbox | Remote — same Toolbox |
| `$HOME` sandbox | `agent/.charter-agent-home/` on this machine | Foundry microVM per project |
| Auth | `az login` identity | User's bearer token via WAM/browser |
| Project isolation | Subfolders under one shared `$HOME` | Separate microVMs per project |
| State persistence | Persists on disk across restarts | Persists in microVM (up to 30-day idle) |

**Important**: Even in local mode, every model call and every WorkIQ tool call goes to the cloud. Local mode is for iterating on agent behaviour with real endpoints — not for offline testing.

### Switching modes at runtime

The header dropdown lets you switch between Local and Hosted during the session. On switch, the client:
- Clears `session_id` and `previous_response_id` (they're endpoint-specific)
- Clears the transcript and activity panel
- Reloads the project list for the new mode (local and hosted projects are tracked separately)

---

## 5. Project management and the sidebar

### Each project is a separate Foundry session

Every project in the left sidebar corresponds to one entry in the client's `projects.json` store, and — in hosted mode — to one dedicated **Foundry session microVM**.

When the client creates a new project:
1. A `project_id` is generated client-side (e.g., `p-9bd98bc8`)
2. The project is saved to `projects.json` with `session_id: null`
3. On the first message, the POST goes to `/responses` with no `agent_session_id`
4. The Foundry platform creates a new microVM and returns an `agent_session_id` in the `response.created` SSE event
5. The client stores this `session_id` against the project in `projects.json`
6. All subsequent messages for this project include `agent_session_id: <stored_session_id>`, routing to the same microVM

The numbered flow above describes the **raw-httpx (legacy)** path, where the client mints the session id itself (it uses the `project_id`) and learns the platform's id from the `response.created` event. The **SDK** path is slightly different: `_ensure_session_sdk()` calls `beta.agents.create_session(isolation_key=<project_id>)` *before* the first turn, so the `agent_session_id` is **server-minted and persisted up front** — there is no "null until first message" window. Either way the result is the same: one stable `agent_session_id` per project, pinned to one microVM, persisted in `projects.json`.

**Switching projects** swaps the active `session_id`. The next POST goes to the microVM for the new project — a completely separate filesystem with separate state.

```
Sidebar project "Northwind SOW"   →  agent_session_id: ses-abc  →  microVM-1  →  $HOME-A
Sidebar project "Fabrikam SOW"    →  agent_session_id: ses-def  →  microVM-2  →  $HOME-B
```

### Idle session handling

Foundry's in-memory transcript store evicts `previous_response_id` after ~15 minutes of idle. The client detects this: if a response comes back with no text and ≤4 SSE events (meaning the model had no context), it automatically retries the same prompt without `previous_response_id`, keeping the `agent_session_id`. This ensures the microVM `$HOME` (which persists much longer) is still reached even if the in-memory transcript rolled.

Additionally, before any send that has been idle for more than 12 minutes, the client pre-emptively clears `previous_response_id` (while keeping `agent_session_id`) to avoid the empty-response scenario.

---

## 6. Local storage and cache

All client-side state is stored in `%USERPROFILE%\.charter-agent\` (Windows) or `~/.charter-agent/` (other platforms).

### `projects.json` — the project registry

Persists the full project list across app restarts, keyed by mode:

```json
{
  "active": { "local": "p-9bd98bc8", "hosted": "p-72fabf49" },
  "projects": {
    "hosted": {
      "p-72fabf49": {
        "label": "Northwind SOW",
        "created_at": "2026-05-20T09:00:00+00:00",
        "last_used_at": "2026-05-25T14:30:00+00:00",
        "session_id": "ses-abc123",
        "previous_response_id": "resp-xyz456",
        "skill": "sow-response",
        "customer_name": "Northwind",
        "is_new": false
      }
    },
    "local": { ... }
  }
}
```

`session_id` and `previous_response_id` are restored on app launch so the next message resumes the correct Foundry session.

### `view_cache.json` — dashboard and activity cache

In hosted mode, the agent's `$HOME` lives in a Foundry microVM that the client cannot read directly (no filesystem access across machines). The client caches the most recent dashboard and activity data after each turn so the UI can restore them on restart:

```json
{
  "hosted/p-72fabf49": {
    "dashboard": { "kind": "dashboard", "project": "Northwind SOW", ... },
    "activity": [ { "at": "...", "actor": "agent", "kind": "kickoff.sent", ... } ],
    "saved_at": "2026-05-25T14:30:00+00:00"
  }
}
```

On project switch or app restart, the cached dashboard and activity are rendered immediately — the user sees the last-known state without needing a new agent turn. The cache is refreshed on every `turn.complete` event.

In local mode, the client reads `activity.json` and `project_log.json` directly from the agent's `$HOME` subfolder on disk, so the cache is less critical but still written for consistency.

### `transcripts/<mode>-<pid>.json` — conversation history

The client-side transcript — all user and agent messages for a project — is saved to a per-project JSON file. On project switch, the transcript is cleared and reloaded from the saved file, so the conversation history persists across switches and restarts.

Capped at 200 turn-pairs (400 messages) per project.

### `auth_record.json` — MSAL authentication record

Saved after first sign-in. Used for silent token refresh on subsequent launches. See [§3](#3-authentication).

---

## 7. SSE streaming and the agent event model

The client drives one streaming turn per send, in a background thread, via `Bridge._post_one` — which dispatches to either the SDK transport (`_post_one_sdk`) or the raw-httpx transport (`_post_one_legacy`); see [§4](#4-calling-the-agent-local-vs-hosted). The SDK path consumes typed Responses events from `responses.create(stream=True)`; the legacy path consumes raw SSE frames parsed by `protocol._iter_sse_events`. Both are normalised to a single dict shape (`protocol._sdk_event_to_dict` for the SDK side) and run through the **same** event switch, so the table below applies to both. Events are pushed to JavaScript via `window.evaluate_js("window.onAgentEvent(msg)")`.

### SSE event types

| Event type | What it signals | Client action |
|---|---|---|
| `response.created` | New response started; carries `agent_session_id` | Store `session_id`; emit `session.update` |
| `response.output_item.added` | A tool call started | Emit `tool.call` with tool name |
| `response.function_call_arguments.delta` | Streaming tool arguments | Buffer; emit `tool.args` when complete |
| `response.function_call_arguments.done` | Tool arguments complete | If `publish_view` tool: extract dashboard payload |
| `response.output_text.delta` | Streaming text | Emit `text.delta`; append to agent message bubble |
| `response.completed` | Turn done; carries final output | Emit `turn.complete` with text + dashboard |
| `oauth_consent_request` (or similar) | First WorkIQ call needs consent | Emit `consent.required`; open browser; retry |
| `*.failed` / `*.error` | Tool or model error | Emit `turn.error`; display in UI |

### JavaScript event handling

The `window.onAgentEvent` function in `assets/app.js` is a switch-based state machine that reacts to each event:

- `tool.call` — adds an activity card to the Activity panel (tool name + animated "running" state)
- `text.delta` — streams text into the current agent bubble, running it through the Markdown renderer
- `turn.complete` — finalises the agent message, renders the dashboard, saves the transcript, refreshes the disk-derived view
- `turn.error` — marks the agent bubble red, displays the error

---

## 8. Dashboard rendering

The dashboard widget shows the live project state: title, status pill, progress bar, per-section owner tiles, exceptions panel, and activity stream.

### Data sources (priority order)

1. **`publish_view` tool arguments** (highest priority) — the agent calls `publish_view(payload=<dashboard>)` as its last step each turn. The client intercepts the tool's arguments from the SSE stream before the response completes and has the structured payload ready for `turn.complete`. This is the canonical source: the agent's declared view of the project state.

2. **Text-fence extraction** (fallback) — if the agent's response contains a ` ```json {"kind":"dashboard",...} ``` ` block, the client parses it out. This catches models that emit JSON in prose rather than through `publish_view`.

3. **`view.update` event** (post-turn) — after `turn.complete`, the client reads the agent's `$HOME` (in local mode) or the cached state (in hosted mode) and emits a `view.update` event with the disk-derived dashboard. This catches any state that the agent wrote but didn't include in `publish_view`.

4. **Cache on startup / project switch** — on app launch or project switch, the last-cached dashboard from `view_cache.json` is rendered immediately so the panel isn't blank.

### Dashboard data flow from agent to UI

```
Agent turn
  └─ calls publish_view(payload={kind:"dashboard", ...})
       └─ SSE: response.function_call_arguments.done
            └─ client captures: published_dashboard = payload
  └─ turn.complete event
       └─ client emits: turn.complete {dashboard: published_dashboard}
            └─ JS renderDashboard(d) → innerHTML replaced
  └─ client saves to view_cache.json
  └─ client reads disk state → emits view.update
       └─ if dashboard in disk state: renderDashboard (overwrites with disk truth)
```

### What the dashboard renders

- **Status pill** — project-level status (`kicked_off`, `in_progress`, `submitted`, `overdue`, `closed`)
- **Owner tiles** — one tile per SOW section: avatar, name ("You" for the signed-in user), task ID, section title, status pill, last signal
- **Progress bar** — `submitted / total` sections as a percentage
- **Exceptions panel** — outstanding issues requiring the SOW Owner's attention
- **Activity stream** — last 20 entries from `activity.json`, newest first

---

## 9. Connection and service status

The header bar shows live status for the agent and WorkIQ services.

### Foundry session pill

Shows whether the current project has a live Foundry session:
- **warm** — `agent_session_id` is set and the last turn completed successfully
- **(none)** — new project, or session was reset

The session ID is displayed in the header as `session: ses-abc123` and updated whenever `response.created` or `response.completed` carries a new ID.

### Endpoint status

The header shows `endpoint: hosted → https://<agent>/responses` (or `local → http://localhost:8088/responses`). The mode dropdown disables options whose endpoint is not configured.

---

## 10. First-turn skill routing

When the user starts a new project, the host (not the client) picks the right workflow skill on the very first turn — there is no client-side auto-trigger or hidden second request.

**How it works:**

1. The user sends a first message ("I received an RFP from Contoso").
2. The host's `_ResilientResponsesHostServer` checks: no preamble `skill=`, no persisted `project_log["skill"]` → it's a brand-new project.
3. The host runs a one-shot LLM classifier against the message and the registered skills' `description` fields, picks the best skill (e.g. `sow-response`), and persists it to `project_log.json`.
4. The host swaps `self._agent` to the warm Agent for that skill and forwards the same turn to it. The user's message is handled by the correct skill immediately — one request, one response.
5. Subsequent turns read the persisted skill from `project_log.json` and skip the classifier entirely.

The user sees: their message → the workflow begins. No placeholder bubble, no "select a skill" prompt, no client-side retry.

Adding a new top-level workflow is purely additive: drop a `SKILL.md` with a good `description` under `agent/skills/<name>/`. The classifier picks it up at boot via `skill_loader`.

---

## 11. Background poller and system tray

### System tray behaviour

The app registers a Win32 system-tray icon (`tray_icon.py`, pure ctypes) when it starts. This changes two behaviours:

- **Closing the window** (clicking X or Alt+F4) hides the window to the tray instead of terminating the process. The background poller and any in-flight agent turns continue uninterrupted.
- **Clicking the tray icon** (left-click) toggles the window back to the screen.
- **Right-clicking the tray icon** shows a context menu with **Show / Hide** and **Quit**. Quit performs a clean exit — it destroys the webview window and terminates the process.

A **single-instance mutex** (`Local\CharterAgent-SingleInstance-v1`) prevents two copies of the app from running at the same time. If a second launch is attempted (manually or via `start.ps1` without `-Force`), it fires a Windows toast ("Already running — look for the tray icon.") and exits immediately.

### Management scripts

The `scripts/` folder provides three PowerShell scripts that manage the app process without opening a console window:

| Script | What it does |
|---|---|
| `start.ps1` | Launches `pythonw.exe app.py` in the background. Detects an existing instance and reports its PID; use `-Force` to kill and restart. |
| `stop.ps1` | Finds the running `pythonw.exe` launched from this project's venv and stops it. |
| `restart.ps1` | Calls `stop.ps1` then `start.ps1 -Force`. |

Process detection is done by matching the exact path of the venv's `pythonw.exe` — not by process name — so it will not accidentally affect unrelated Python processes.

### Background poller

The `AutoPoller` class (`app.py`) wakes up on a configurable interval and runs an autonomous check turn against every project whose active skill declares `background_sync: true` in its `SKILL.md` frontmatter. Currently only the `sow-response` skill has this flag set.

**Interval:** controlled by `CHARTER_POLL_INTERVAL_MINS` in `desktop-client/.env` (default 30 minutes). Set it to a low value (e.g., `2`) for testing and restore before production use.

**Business hours restriction:** set `CHARTER_POLL_BIZ_HOURS_ONLY=1` to restrict polling to Mon–Fri 07:00–20:00 local time.

**Scheduler status bar** (header, bottom-right):
- Grey countdown — idle, shows time until next check
- Amber "Starting auto-check…" — check is about to start (past due, not yet running)
- Blue pulse — check actively running
- Green "Updated" — last check found new replies

**"Run now ↻" button:** triggers an immediate check for the active project, using the same code path as the scheduled poller (fires a toast notification and updates the scheduler bar on completion). The button is disabled while the check is running.

**Notifications:** on completion, a Windows toast is shown regardless of whether new replies were found. The toast text summarises any changes; "No new replies" confirms the check ran cleanly.

---

## 12. Architecture: Bridge pattern

The `Bridge` class in `charter_client/bridge.py` is the Python-side coordinator exposed to JavaScript via pywebview's `js_api`. Every button, dropdown, and send action in the UI calls a method on `Bridge`. (`app.py` constructs the single `Bridge` instance and passes it as the window's `js_api`; it holds no logic of its own.)

### Key Bridge methods (callable from JavaScript)

| Method | What it does |
|---|---|
| `ready()` | Returns initial context (session, user, projects, view) on app boot |
| `signin_silent()` | Attempts silent token refresh using saved `AuthenticationRecord` |
| `login()` | Interactive sign-in via WAM or browser |
| `send(prompt, skill?)` | Starts a turn: refreshes token, dispatches to the SDK or raw-httpx transport via `_post_one`, streams the Responses events |
| `run_now()` | Triggers an immediate background-poller check for the active project; emits `scheduler.tick` / `scheduler.done` and fires a toast — same path as the scheduled poller |
| `new_project()` | Creates a new project entry, activates it |
| `switch_project(id)` | Swaps `session_id` / `previous_response_id` to the selected project |
| `delete_project(id)` | Removes project from store; in hosted mode, attempts to delete the Foundry session microVM |
| `set_mode(mode)` | Switches between `local` and `hosted` endpoint |
| `reset_session()` | Clears `previous_response_id` (keeps `session_id` and `$HOME`) |
| `project_view(id)` | Reads disk state (local) or cache (hosted) and returns dashboard + activity |

### Event flow: JavaScript → Python → SSE → JavaScript

```
User clicks Send
  → JS: sendPrompt(text)
  → JS: window.pywebview.api.send(text)     [async call to Python Bridge]
  → Python: Bridge.send()
       → refresh token if needed
       → spawn _run_turn in thread pool
       → return {ok: true}
  → Python thread: _post_one()              [transport dispatcher]
       → _post_one_sdk()  (hosted + SDK available)   responses.create(stream=True)
         …or _post_one_legacy()  (local / fallback)  httpx POST /responses (stream=true)
       → for each Responses event (normalised to one dict shape):
            → self._emit("text.delta", {delta: "..."})
                 → window.evaluate_js("window.onAgentEvent({event:'text.delta',...})")
                      → JS: currentAgentBuffer += delta; renderAgentText()
       → _finalize_turn()  [shared tail for both transports]
       → self._emit("turn.complete", {...dashboard, text, response_id})
  → JS: turn.complete handler
       → renderDashboard(); saveTranscript(); refreshView()
```

The Bridge uses a threading lock (`self._lock`) so only one turn runs at a time per `Bridge` instance. The UI disables the send button (`setBusy(true)`) for the duration.
