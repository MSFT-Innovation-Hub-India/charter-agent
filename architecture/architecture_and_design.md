# Architecture & Design — charter-agent

> The implementation-level companion to [`../functional-specs/project_workspace_spec.md`](../functional-specs/project_workspace_spec.md). The requirement spec answers *what* and *why*. This document answers *how* — concrete components, contracts, sequences, schemas, and the seams where Copilot-generated code plugs into otherwise generic agent code.
>
> Read [`../AGENTS.md`](../AGENTS.md) first for the non-negotiable invariants this design has been shaped around.

**Status**: draft v0.1 — pre-implementation. Expect revisions during Phase 1–3 as the Foundry SDK behaviour gets pinned down against real responses.

---

## 1. Design principles, restated as decisions

These are the principles from the requirement spec, expressed as the design decisions they directly imply. Every later section is a consequence of these.

| Principle (from spec) | Concrete design decision |
|---|---|
| **Skills-first, agentskills.io-conformant** | All reusable agent capabilities (classify, draft, validate, consolidate, propose, render) are packaged as **Agent Skills** under `agent/skills/{name}/`, each containing a `SKILL.md` valid per the open [agentskills.io spec](https://agentskills.io/specification) — required YAML `name`/`description`, optional `metadata`/`license`/`compatibility`/`allowed-tools`, optional `scripts/`/`references/`/`assets/` subdirs, progressive disclosure (discovery → activation → execution). The Copilot SDK's `skills/*/SKILL.md` auto-loader is the runtime. See [AGENTS.md §4.3](../AGENTS.md#43-agent-skills-format-agentskillsio-conformance) for the format contract and [§4.4](../AGENTS.md#44-core-code-vs-skill--the-decision-rule) for the core-vs-skill decision rule. |
| Generic over project-specific | Three layers of variability: **Charter (data)** → **Agent Skills (declarative behaviour)** → **optional Copilot-generated `consolidator.py` (deterministic code, exceptional)** → **generic agent (constant)**. Nothing else varies per project. |
| WorkIQ is delegated-only | All WorkIQ calls funnel through one wrapper module that requires an OBO token argument; no path can call WorkIQ without one. |
| No background workers | The agent has *one* entry point — `handle_invocation(action, payload, obo_context)`. Every behaviour, including dashboard refresh, is reached from there. |
| State lives in `$HOME` | A single `state.py` module is the only place that touches files in `$HOME`. Atomic write-via-temp-then-rename for every mutation. |
| Charter immutability | `charter.json` is read by everything; written only by `charter/ratify.py` and `charter/amend.py`. Enforced by a module-level lock plus a CI test that greps for forbidden writers. |
| Human-in-the-loop outbound | A `SuggestedAction` is the only path to an outbound side-effect. Two functions: `draft(...)` (returns a `SuggestedAction`, writes to state) and `execute(action_id, coordinator_obo)` (idempotent, OBO-as-coordinator). |
| Channel extensibility | Channel handlers register with `@register_channel("sharepoint_file")` decorators; the capture loop iterates the registry; new channels are additive. |
| Single agent runtime | Exactly one `CopilotClient` per process, owned by `copilot_runtime.py`; one Copilot session per Foundry session, resumed via `FOUNDRY_AGENT_SESSION_ID`. No second LLM call path. See [AGENTS.md §3 invariant 12](../AGENTS.md). |

---

## 2. Component diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Tenant boundary                                 │
│                                                                              │
│   Browser ── HTTPS ──► Azure Container App ── Foundry API ──► Foundry        │
│     (SSO)              (FastAPI BFF + SPA)   (/invocations)   Hosted Agent   │
│                              │                                    │          │
│                              │                                    │          │
│                  reads /p/{project_id} from URL,                   │          │
│                  passes as x-ms-chat-isolation-key,                │          │
│                  forwards user's OBO token                         │          │
│                                                                    ▼          │
│                                                       ┌───────────────────┐  │
│                                                       │ Session microVM   │  │
│                                                       │  $HOME/           │  │
│                                                       │    charter.json   │  │
│                                                       │    state.json     │  │
│                                                       │    activity.json  │  │
│                                                       │    code/          │  │
│                                                       │      consolidator.py │
│                                                       └───────────────────┘  │
│                                                                  │   │       │
│                                              ┌───────────────────┘   │       │
│                                              │                       │       │
│                                              ▼                       ▼       │
│                              ┌─────────────────────────┐   ┌────────────────┐│
│                              │  Foundry Toolbox        │   │ GHCP Copilot   ││
│                              │  (MCP endpoint that     │   │ SDK            ││
│                              │   bundles all WorkIQ    │   │ (THE runtime;  ││
│                              │   MCP servers) —        │   │  warm one      ││
│                              │   bridged into the      │   │  CopilotClient ││
│                              │   Copilot session via   │   │  per process;  ││
│                              │   McpBridge             │   │  session       ││
│                              │                         │   │  resumed via   ││
│                              │   • Mail                │   │  FOUNDRY_      ││
│                              │   • Calendar            │   │  AGENT_SESSION_││
│                              │   • Files (SP/OneDrive) │   │  ID; runs on   ││
│                              │   • Teams               │   │  GHCP default  ││
│                              │   • workiq.ask          │   │  model via     ││
│                              │                         │   │  GITHUB_TOKEN. ││
│                              │  Identity passthrough   │   │  Also generates││
│                              │  → visiting user's OBO  │   │  consolidator. ││
│                              │                         │   │  py (exception)││
│                              └─────────────────────────┘   └────────────────┘│
│                                                                              │
│                                                                              │
│  ── App Insights / OpenTelemetry ── (auto-injected; all spans + activity)    │
└─────────────────────────────────────────────────────────────────────────────┘
```

Two surfaces are public: the Container App URL and the Foundry agent's `/invocations` endpoint. Everything else is private to the tenant.

---

## 3. Repository layout — the contract

The target layout is defined in [`../AGENTS.md` §5](../AGENTS.md). This section adds the *responsibility* of each module so PRs can be reviewed against intent, not just structure.

### 3.1 `agent/src/charter_agent/`

| Module | Responsibility | Imports allowed |
|---|---|---|
| `__main__.py` | Wire `azure-ai-agentserver-invocations` to `orchestrator.handle_invocation`. Warm the singleton `CopilotClient` via `copilot_runtime.bootstrap()`. Assert env-var policy (`GITHUB_TOKEN` present, `AZURE_AI_MODEL_DEPLOYMENT_NAME` absent). Nothing else. | `azure_ai_agentserver_invocations`, `copilot_runtime`, `orchestrator` |
| `copilot_runtime.py` | **Sole owner of `CopilotClient`.** One warm instance per process. One Copilot session per Foundry session, resumed via `FOUNDRY_AGENT_SESSION_ID`. Owns the tool registry exposed to the Copilot session (Toolbox tools via `McpBridge` + any agent-internal Python tools). | `copilot` (SDK), `workiq` (for the bridge) |
| `orchestrator.py` | One function per action verb (§7). Translates the verb into a prompt/instruction sent on the Copilot session; never implements business logic itself. | All of the below |
| `charter/` | Pydantic schema, JSON Schema export, ratification flow, amendment flow. **Sole writer of `charter.json`**. | `state`, `workiq` (for grounding), `codegen` (regen `consolidator.py` on amend) |
| `kickoff/` | Fan-out actions on a freshly ratified Charter: SharePoint folder + templated files, Teams kickoff message, Outlook tasks, briefing emails. | `workiq`, `actions`, `state` |
| `capture/` | Channel-handler registry + handlers (one file per `channel.kind`). The classifier itself is a **skill** (`agent/skills/capture-classify/`), invoked via the Copilot session. | `workiq`, `state`, `copilot_runtime` |
| `status/` | Pure functions: given submissions + channel signals + calendar + Charter, return per-task status. **No side effects.** | (stdlib only) |
| `actions/` | `SuggestedAction` lifecycle: draft (via the `draft-outbound` skill on the Copilot session), persist, execute, mark-executed. Idempotency lives here. | `workiq`, `state`, `copilot_runtime` |
| `codegen/` | **Exceptional** path: generates `$HOME/code/consolidator.py` at kickoff and on amendment, by acquiring a Copilot session from `copilot_runtime`. No client construction here. | `copilot_runtime`, `state` |
| `consolidation/` | Loads the generated `consolidator.py`, runs it, surfaces findings. | `state`, generated module |
| `state.py` | Atomic read/write of every `$HOME` file. Returns Pydantic models. | (stdlib only) |
| `workiq/` | (a) Toolbox MCP client + `McpBridge` exposing Toolbox tools to the Copilot session; (b) thin async helpers for direct Python calls when the orchestrator needs raw tool output (kickoff fan-out, channel polling). Every function takes `obo_context` as the first argument. | MCP client SDK, `azure-identity` |
| `observability.py` | `span(name, **attrs)` context manager that opens an OTel span *and* appends to `activity.json`. The Invocations protocol library already emits root spans for `/invocations`; this layer adds the per-step children. | `opentelemetry`, `state` |

The dependency direction is enforced in CI with `import-linter`. Cycles or upward imports fail the build.

---

## 4. Runtime sequences

Each sequence below maps to one action verb in §7. Sequences are the canonical reference when implementing a phase; if your implementation deviates, update the sequence in the same PR.

### 4.1 Kickoff (action verb: `propose_charter` → `ratify_charter`)

```
Coordinator                  Frontend (BFF)        Foundry Agent           WorkIQ (via Toolbox)        Copilot SDK
     │                            │                      │                          │                        │
     │ types prompt + selects     │                      │                          │                        │
     │ "kickoff"                  │                      │                          │                        │
     │ ──────────────────────────►│                      │                          │                        │
     │                            │ POST /invocations    │                          │                        │
     │                            │ action=propose_charter│                         │                        │
     │                            │ (chat_iso=project_id, │                         │                        │
     │                            │  user OBO token)      │                         │                        │
     │                            │ ─────────────────────►│                          │                        │
     │                            │                      │ load similar prior       │                        │
     │                            │                      │ artifacts (last month's  │                        │
     │                            │                      │ board pack, runbook)     │                        │
     │                            │                      │ ────────────────────────►│                        │
     │                            │                      │◄─────────────────────────│                        │
     │                            │                      │ draft Charter JSON       │                        │
     │                            │ proposed Charter ◄───│                          │                        │
     │ proposed Charter ◄─────────│                      │                          │                        │
     │                            │                      │                          │                        │
     │ amends ("Day 4 not 5") +   │                      │                          │                        │
     │ "ratify"                   │                      │                          │                        │
     │ ──────────────────────────►│                      │                          │                        │
     │                            │ POST /invocations    │                          │                        │
     │                            │ action=ratify_charter│                          │                        │
     │                            │ (with edits)         │                          │                        │
     │                            │ ─────────────────────►│                          │                        │
     │                            │                      │ validate, version=1,     │                        │
     │                            │                      │ write charter.json       │                        │
     │                            │                      │                          │                        │
     │                            │                      │ codegen consolidator.py  │                        │
     │                            │                      │ (exceptional; only       │                        │
     │                            │                      │  module that needs       │                        │
     │                            │                      │  deterministic Python.   │                        │
     │                            │                      │  Renderer & compliance   │                        │
     │                            │                      │  are skills, not code.)  │                        │
     │                            │                      │ ────────────────────────────────────────────────►│
     │                            │                      │◄────────────────────────────────────────────────│
     │                            │                      │                          │                        │
     │                            │                      │ kickoff fan-out          │                        │
     │                            │                      │  ▸ create SP folder      │                        │
     │                            │                      │  ▸ for each task:        │                        │
     │                            │                      │     create templated     │                        │
     │                            │                      │     file w/ perms        │                        │
     │                            │                      │     send briefing email  │                        │
     │                            │                      │     create Outlook task  │                        │
     │                            │                      │  ▸ post kickoff Teams    │                        │
     │                            │                      │    message               │                        │
     │                            │                      │ ────────────────────────►│ (all as coordinator)   │
     │                            │ kickoff complete ◄───│                          │                        │
     │ dashboard ◄────────────────│                      │                          │                        │
```

### 4.2 Dashboard refresh (action verb: `render_dashboard`)

This is the **autonomous capture loop** ([spec §8](../functional-specs/project_workspace_spec.md)). It runs on every visit. For an observer's visit, the capture cycle is shorter (their visibility is narrower); for the coordinator's visit, it runs in full and the suggested-action drafter is engaged.

```
1. load charter.json, state.json
2. identify visiting user from OBO context
3. for each task:
     for each watch_channel:
         handler = registry[channel.kind]
         events = handler.poll(charter, task, channel, since=state.last_check[task_id], obo)
         for event in events:
             # classification via the `capture-classify` skill on the warm Copilot session
             classification = copilot_runtime.run_skill("capture-classify", event=event, task=task, charter=charter)
             match classification:
                 case submission | revised_submission:
                     content = workiq.extract(event, obo)
                     # compliance check via the `compliance-check` skill (no generated .py module)
                     results = copilot_runtime.run_skill(
                         "compliance-check", submission=content, requirements=task.runbook_requirements,
                     )
                     state.record_submission(task_id, event, content, results)
                 case question:
                     state.add_exception(...)
                 case supporting | unrelated:
                     state.record_seen(event)
     state.last_check[task_id] = now
     status = status.triangulate(state.submissions[task_id], state.channel_signals[task_id], workiq.calendar(owner, obo))
     state.set_status(task_id, status)
4. consolidation gate:
     if all(task.status in (Submitted, SubmittedWithGaps) for task in charter.tasks):
         findings = generated_consolidator.consolidate(charter, state)
         state.record_consolidation(findings)
5. if visiting_user == coordinator:
     for task in charter.tasks:
         maybe_draft_action(task, state)   # via the `draft-outbound` skill
6. payload = copilot_runtime.run_skill("render-dashboard", charter=charter, state=state, viewing_user=visiting_user)
7. return payload
```

Cold-start delay (post-15-min idle) shows as a "warming up…" state in the frontend for the first 2–5 seconds.

### 4.3 Approve a suggested action (action verb: `execute_suggested`)

```
Coordinator clicks "Approve nudge to Marcus"
  → Frontend POSTs {action: "execute_suggested", approve_action_id: "<uuid>"}
  → Agent:
      1. load state.json
      2. if approve_action_id in state.executed_action_ids:  return idempotent OK
      3. action = state.suggested_actions[approve_action_id]
      4. workiq.send_teams_message(action.payload, obo=coordinator)   ← in coordinator's OBO
      5. state.executed_action_ids.add(approve_action_id)
      6. state.suggested_actions[approve_action_id].status = "executed"
      7. log activity (with coordinator identity, action UUID, target)
  → render_dashboard (§4.2) on the way out
```

### 4.4 Charter amendment (action verb: `amend_charter`)

```
1. validate amendment (no orphan deps, no conflict with executed work)
2. compute diff vs current charter.json
3. write charter.json with version += 1, ratified_at = now, ratified_by = coordinator
4. if `tasks[]` set changed, templates changed, or any `consolidation_rules` changed → regen `$HOME/code/consolidator.py` via `codegen.generate_module("consolidator", charter)`. Renderer + compliance behaviour come from skills and need no regeneration.
5. fan out kickoff actions for newly added tasks
6. revoke / re-grant SP permissions for re-owned tasks
7. log amendment with diff to activity.json
```

### 4.5 Project close (action verb: `close_project`)

```
1. require: state.consolidation.findings_resolved == True
2. export final deliverable to charter.deliverable.output_location (SharePoint)
3. archive state.json + activity.json + Charter to SharePoint alongside the deliverable
4. mark session "closed" in state.json
5. set short retention timer (frontend stops accepting writes; reads still work)
6. emit "project_closed" telemetry event
```

The session itself dissolves naturally via the 30-day session ceiling; the archived artifacts in SharePoint are the durable record.

---

## 5. Data contracts (authoritative schemas)

These are the Pydantic models that go in `agent/src/charter_agent/charter/schemas.py`. The JSON projections are what gets persisted to `$HOME`. If you change a schema, the change must be made to both the Pydantic model and this document in the same commit.

### 5.1 `charter.json` (v1)

The canonical schema is in [requirement spec §6.2](../functional-specs/project_workspace_spec.md). One nuance to record here that the requirement spec doesn't:

- `project_kind` is a **descriptor for humans and prompts** — not a discriminator for code branches. Do not write `if charter.project_kind == "board_pack": …` anywhere. Its only legitimate uses are (a) display, (b) the Copilot session's skill context, (c) the WorkIQ "find similar prior artifacts" grounding query.
- `watch_channels[].kind` is the discriminator that *does* matter — it must match a key in the channel-handler registry (§6.2). Validation in `charter/ratify.py` checks every channel kind against the registry at ratification time.
- The fields `compliance_module_path` and `renderer_module_path` from the v0 schema are **retired** under the skills-first runtime. Only `consolidator_module_path` remains and points to the (optionally generated) `$HOME/code/consolidator.py`.

### 5.2 `state.json`

```json
{
  "schema_version": 1,
  "project_id": "board-nov-2025",
  "session_started_at": "2025-11-14T09:14:00Z",
  "last_full_refresh_at": "2025-11-19T16:22:00Z",
  "last_full_refresh_by": "chief.of.staff@firm.com",

  "tasks": {
    "fin": {
      "status": "Submitted",            // Assigned | InProgress | Submitted | SubmittedWithGaps | Overdue
      "last_check": "2025-11-19T16:22:00Z",
      "submissions": [
        {
          "submission_id": "sub_4a7…",
          "received_at": "2025-11-19T15:08:00Z",
          "source": {"channel": "sharepoint_file", "ref": "Board-Nov-2025/Financial_Summary_DRAFT.xlsx"},
          "extracted_summary": "Rev $77.4M, margin 18.2%, top-5 total $77.4M",
          "compliance": [
            {"requirement_id": "rev_mom", "status": "met", "evidence": "Cell B7 shows MoM +3.1%"},
            {"requirement_id": "margin_mom", "status": "met", "evidence": "Cell B8 shows MoM +0.4 ppt"},
            {"requirement_id": "top5_total", "status": "met", "evidence": "Sheet 'Top5' SUM=$77.4M"}
          ]
        }
      ],
      "channel_signals": {
        "teams_completion": {"at": "2025-11-19T15:11:00Z", "snippet": "done — please review"},
        "email_completion": null,
        "owner_availability": "available"   // available | ooo | tentative
      }
    }
  },

  "exceptions": [
    {
      "exception_id": "exc_8c2…",
      "raised_at": "2025-11-19T16:22:00Z",
      "kind": "needs_review_classification",
      "task_id": "fin",
      "summary": "Anita's email mentioned a draft attached but no file was attached",
      "visible_to": ["chief.of.staff@firm.com"]
    }
  ],

  "suggested_actions": {
    "act_3d1…": {
      "action_id": "act_3d1…",
      "drafted_at": "2025-11-19T16:22:00Z",
      "kind": "nudge_owner",                // nudge_owner | clarify_gap | propose_reassign | propose_amendment
      "target": {"upn": "marcus@firm.com", "channel": "teams"},
      "reason": "Talent section due in 8h; no activity in 4 days; Marcus is available",
      "draft_payload": "Hi Marcus — quick check…",
      "status": "pending"                   // pending | approved | executed | dismissed
    }
  },
  "executed_action_ids": ["act_b91…", "act_7e3…"],

  "consolidation": {
    "last_run_at": "2025-11-20T17:45:00Z",
    "output_path": "sharepoint:Board-Nov-2025/Final/BoardPack_Nov2025_v1.docx",
    "findings": [
      {"id": "fnd_1", "kind": "cross_section_numeric_mismatch", "detail": "Top-5 $77.4M (Fin) vs $76.2M (Cli)", "resolved": false}
    ]
  },

  "closed": false
}
```

### 5.3 `CandidateEvent` (from a channel handler)

```python
class CandidateEvent(BaseModel):
    event_id: str                  # stable, dedup key (channel-specific generation)
    channel: str                   # the channel.kind that produced this
    occurred_at: datetime          # source-system timestamp
    actor: EmailAddress            # who produced the event
    summary: str                   # one-line human description for activity log
    payload_ref: dict              # opaque-to-orchestrator; channel handler interprets
```

### 5.4 `SuggestedAction` invariants

- `action_id` is a UUIDv4 generated at draft time.
- Once `status` ∈ `{executed, dismissed}`, the action is immutable.
- `execute_suggested` must check `action_id in state.executed_action_ids` *before* the WorkIQ side-effect call, not after.

### 5.5 `activity.json`

Append-only. One JSON object per line. Schema:

```json
{"at": "ISO-8601", "actor": "upn-or-agent", "kind": "kickoff|capture|classify|draft_action|execute_action|amend|consolidate|close", "ref": "task_id-or-action_id", "summary": "human-readable one-liner", "span_id": "OTel span id"}
```

The `span_id` field is how human auditors trace from the human-readable log back to App Insights.

---

## 6. Pluggable layers

### 6.1 The Copilot-generated module interface

Only **one** module is generated under the skills-first runtime: `$HOME/code/consolidator.py`. The renderer and compliance behaviours from the v0 design are now skills (`agent/skills/render-dashboard/`, `agent/skills/compliance-check/`) and produce their output directly from the Copilot session. Generate a Python module only when the work needs deterministic running code (template-specific Word/Excel stitching, cross-section numeric reconciliation).

```python
# consolidator.py
def consolidate(charter: dict, state: dict) -> dict:
    """Returns {'output_path': str, 'reconciliation_findings': [ ... ]}."""
```

After codegen, the module is **import-tested** before being committed to `$HOME`. The codegen wrapper:

1. Asks `copilot_runtime` for a session, sends the prompt with instructions to write the file to `$HOME/code/_staging/consolidator.py`.
2. Imports the staged file in an isolated subinterpreter.
3. Calls a smoke-test fixture against the function signature.
4. On success, moves to `$HOME/code/consolidator.py` atomically.
5. On failure, retries the Copilot prompt with the error appended once; on second failure, raises and surfaces the codegen failure as an exception in `state.exceptions`.

### 6.2 Channel handler registry

```python
# agent/src/charter_agent/capture/registry.py
_HANDLERS: dict[str, ChannelHandler] = {}

def register_channel(kind: str):
    def deco(cls):
        _HANDLERS[kind] = cls()
        return cls
    return deco

def get_handler(kind: str) -> ChannelHandler:
    return _HANDLERS[kind]

class ChannelHandler(Protocol):
    async def poll(
        self,
        charter: Charter,
        task: Task,
        channel: WatchChannel,
        since: datetime,
        obo: OBOContext,
    ) -> list[CandidateEvent]: ...
```

Initial handlers (one file per kind, all in `capture/handlers/`). The `WorkIQ tool` column names match the tools exposed by the `Charter-Agent-Tools` Foundry Toolbox; the exact tool input schemas are discovered at runtime via `tools/list` against the Toolbox MCP endpoint (see [§8.1](#81-foundry-toolbox-bridge-pattern)).

| `kind` | File | Underlying Toolbox tool(s) |
|---|---|---|
| `sharepoint_file` | `sharepoint_file.py` | `WorkIQSharePoint2` (file versions / activity for a path) |
| `email_from` | `email_from.py` | `WorkIQMail2` (list received-from address since cursor) |
| `teams_thread` | `teams_thread.py` | `WorkIQTeams` (channel messages since cursor, filtered by author) |
| `onedrive_share` | `onedrive_share.py` | `WorkIQOneDrive` (shares from user since cursor) |

Additional Toolbox tools available for non-channel use: `WorkIQCalendar2` (used by `status/triangulate` for OOO detection), `WorkIQUser` (resolves UPNs at ratification), `WorkIQWord` (used by the generated `consolidator.py`), and `WorkIQCopilot` (the natural-language `workiq.ask`-style fallback for grounding queries at kickoff and amendment).

> The exact tool list and schemas above reflect the Toolbox at the time of writing. **Always query the live Toolbox** (`tools/list`) at agent boot — and cache the result for the lifetime of the process — rather than hard-coding tool schemas. See [§8.1](#81-foundry-toolbox-bridge-pattern).

A new channel kind only needs: (1) a new handler file with a `@register_channel(...)` decorator, (2) an entry in this table, (3) accepted by the JSON Schema enum in `charter/schemas.py`. No changes to the capture loop.

### 6.3 Classifier contract

The classifier is **a Copilot SDK skill** (`agent/skills/capture-classify/SKILL.md`), not a generated module and not a separate LLM call path. The capture loop invokes it via `copilot_runtime.run_skill("capture-classify", ...)`, which feeds the skill's instructions plus the inputs below into the warm Copilot session and parses a structured response.

Input: a `CandidateEvent` + the relevant `Task` + a small slice of the Charter (`task.runbook_requirements`, `task.expected_artifact_type`).

Output:

```python
class Classification(BaseModel):
    label: Literal["submission", "revised_submission", "question", "supporting_material", "unrelated"]
    confidence: float            # 0..1
    rationale: str               # short, included in activity log
    needs_review: bool           # true when confidence < threshold (default 0.7)
```

A `needs_review=True` outcome creates an `exception` of `kind="needs_review_classification"` for the coordinator.

---

## 7. Frontend ↔ Agent action verb contract

The agent's `/invocations` endpoint accepts a JSON envelope:

```json
{ "action": "<verb>", "payload": { ... } }
```

Every response is also a JSON envelope:

```json
{ "ok": true, "action": "<verb>", "result": { ... }, "dashboard": { ... optional ... } }
```

The dashboard payload is included whenever the action mutates state, so the frontend gets a fresh render in the same round-trip.

| Action verb | Payload | Who can call | Notes |
|---|---|---|---|
| `propose_charter` | `{prompt: str}` | coordinator | Returns the proposed Charter; nothing persisted yet. |
| `ratify_charter` | `{charter: Charter}` (possibly edited) | coordinator | Persists, runs codegen, runs kickoff fan-out. |
| `render_dashboard` | `{}` | anyone with SSO | Runs the capture loop in caller's OBO. |
| `execute_suggested` | `{approve_action_id: str}` | coordinator | Idempotent. |
| `dismiss_suggested` | `{approve_action_id: str, reason: str}` | coordinator | Sets `status=dismissed`. |
| `amend_charter` | `{amendment: AmendmentSpec}` | coordinator | Re-runs ratification + selective regen. |
| `override_capture` | `{task_id, submission_id, action: "unmark"}` | coordinator | Implements [spec §10.12](../functional-specs/project_workspace_spec.md). |
| `coordinator_chat` | `{message: str}` | coordinator | Free-text instruction; agent reasons and may call any of the above verbs internally. |
| `close_project` | `{}` | coordinator | Runs the close sequence (§4.5). |

Authorisation: the agent compares the OBO subject UPN to `charter.stakeholders.coordinator` to gate coordinator-only verbs. The frontend additionally hides those controls for non-coordinators, but the agent is the authority.

---

## 8. Copilot SDK runtime and Toolbox bridge

### 8.1 Foundry Toolbox bridge pattern

The `agent/src/charter_agent/workiq/` module — the only path through which the agent calls WorkIQ — connects to the **`Charter-Agent-Tools` Foundry Toolbox** over its MCP-compatible endpoint and exposes the tools into the Copilot session via an `McpBridge`. The working reference for the bridge lives at [`architecture/samplecode_toolbox.py`](samplecode_toolbox.py), generated from the Foundry portal's "Call this toolbox" panel and modelled on Microsoft Learn's "Use Foundry Toolboxes from the Copilot SDK" (https://aka.ms/foundry-toolbox-copilotsdk).

Reuse the patterns from the sample verbatim where they apply. They are **not optional details** — each one is a hard requirement of the Toolbox MCP endpoint as it stands today:

| Pattern from sample | Detail | Where it lives in our code |
|---|---|---|
| Endpoint URL | `{project_endpoint}/toolboxes/Charter-Agent-Tools/versions/{ver}/mcp?api-version=v1` in dev, `/mcp?api-version=v1` (consumer) in prod | `workiq/toolbox_client.py` config (env var `TOOLBOX_MCP_ENDPOINT` — **not** `FOUNDRY_TOOLBOX_ENDPOINT`, the `FOUNDRY_` prefix is reserved) |
| Auth | `DefaultAzureCredential().get_token("https://ai.azure.com/.default").token` | `workiq/toolbox_client.py._get_token()` |
| Required header | `Foundry-Features: Toolboxes=V1Preview` on every request | `workiq/toolbox_client.py._headers()` |
| MCP handshake | POST `initialize` → capture `mcp-session-id` from response headers → POST `notifications/initialized` | `ToolboxMcpClient.initialize()` |
| Session continuity | Include `mcp-session-id` header on every subsequent call | `ToolboxMcpClient._request_headers()` |
| Tool discovery | `tools/list` once at agent boot; cache schemas per-process | `workiq/tool_registry.py` |
| Tool invocation | `tools/call` with `{name, arguments}`, **always streamed** (`stream=True`) — non-streaming is not supported by the Toolbox | `ToolboxMcpClient.call_tool()` |
| Tool-name sanitisation | Copilot SDK rejects `.` and `-` in tool names; substitute `_` *only* at the Copilot-SDK boundary, never when calling MCP directly | `workiq/mcp_bridge.py` |
| Disable prompts/ping | Construct the Copilot client with prompts disabled; never call `send_ping()` against the Toolbox (returns 500) | `copilot_runtime.bootstrap()` |

**How the bridge plugs into the runtime.** At `copilot_runtime.bootstrap()` the agent (a) opens one persistent MCP session to the Toolbox via `ToolboxMcpClient.initialize()`, (b) calls `tools/list` once and caches the schemas, (c) wraps each MCP tool as a Copilot tool definition with the sanitised name, (d) registers a `tool_handler` callback that routes Copilot tool calls back through `ToolboxMcpClient.call_tool()` with the per-invocation OBO context attached.

**Standing rule when extending beyond the sample.** The sample is a snapshot. For anything not directly shown in it — a new Toolbox tool, a different MCP method, a changed input schema, a new auth header, a bumped MCP `protocolVersion`, a new server capability — do **not** infer from the sample. Instead:

1. Call `tools/list` against the **live** Toolbox endpoint and inspect the returned schemas; this is the authoritative source for tool names, descriptions, and parameter shapes.
2. For protocol-level questions (new MCP methods, capability negotiation), inspect the response of `initialize` against the live endpoint; the server advertises what it actually supports.
3. For breaking changes (header renames, scope changes, API-version bumps), update [`samplecode_toolbox.py`](samplecode_toolbox.py) from the Foundry portal's regenerated sample in the same PR, and update the table above.
4. Reference the Microsoft Learn docs cited in [references.md §8](../functional-specs/references.md) for documented behaviours; the live server overrides docs if they conflict.

Never hard-code a tool's input schema in our source. Schemas come from `tools/list` and flow through to the Copilot SDK tool definitions (sample shows the pattern in `_make_copilot_tools()`).

### 8.2 Copilot runtime ownership and the exceptional codegen path

`copilot_runtime.py` is the **only** module allowed to instantiate `CopilotClient`. It exposes three responsibilities to the rest of the agent: (a) the warm singleton client, (b) per-Foundry-session session resume (`get_or_create_session(FOUNDRY_AGENT_SESSION_ID)`), (c) the `run_skill(name, **inputs)` helper that the orchestrator/capture/actions layers use for skill-driven reasoning.

`codegen/` is the **exceptional** path. It is the only place that asks the Copilot session to *write code into `$HOME/code/`* rather than answer in-band. Today it generates a single module — `consolidator.py` — and only when consolidation logic genuinely needs deterministic Python (template-specific Word stitching, cross-section numeric reconciliation). See [AGENTS.md §3 invariant 1](../AGENTS.md) and [§4.2](../AGENTS.md#42-model-assignment-policy).

```python
# codegen/generate.py
async def generate_module(module_name: Literal["consolidator"], charter: Charter) -> Path:
    prompt = PROMPTS[module_name].render(charter=charter)
    session = await copilot_runtime.get_or_create_session()  # borrows the warm client + session
    staging = HOME / "code" / "_staging" / f"{module_name}.py"
    staging.parent.mkdir(parents=True, exist_ok=True)
    await session.send_and_wait(
        f"{prompt}\n\nWrite the result to {staging}",
        timeout=180.0,
    )
    validate_module(staging, module_name)
    return move_atomically(staging, HOME / "code" / f"{module_name}.py")
```

Properties:

- **One warm client, one resumed session per Foundry session.** Codegen does not create a separate Copilot session; it borrows the agent's session. Conversational state is intentionally shared because the Copilot session already knows the Charter from kickoff.
- **Validation before promotion.** Import + signature check + smoke fixture; failure triggers one retry with error context appended to the prompt.
- **Telemetry.** Each generation emits an OTel span `codegen.generate_module` with attributes `module`, `charter_version`, `attempt`, `outcome`. The Invocations protocol library auto-emits the parent `/invocations` span.
- **Source preserved.** The generated file is left in `$HOME/code/` with a header comment `# auto-generated for charter v{n} at {ts}`. The activity log records the path; an auditor can read the actual file later.

Prompt templates are versioned alongside the code; changing a prompt is a code change, reviewed like any other.

---

## 9. Observability & audit

Two layers, always emitted together by the `observability.span(...)` context manager:

1. **App Insights / OTel span** — for ops dashboards, error analysis, latency tracking.
2. **`$HOME/activity.json` entry** — for the project's narrative audit trail, exportable to SharePoint at close.

Standard span attributes for every span:

| Attribute | Source |
|---|---|
| `project_id` | from chat-isolation key |
| `actor.upn` | from OBO token; `system` for agent-internal spans |
| `actor.role` | `coordinator` / `owner` / `observer` / `system` |
| `charter.version` | from `charter.json` |
| `verb` | the action being handled, if applicable |

Recommended named spans (non-exhaustive):

- `invocation.handle` (root span per `/invocations`)
- `charter.propose`, `charter.ratify`, `charter.amend`
- `kickoff.fanout`, `kickoff.create_templated_file`, `kickoff.post_teams`, `kickoff.send_email`, `kickoff.create_task`
- `capture.poll_channel` (with attribute `channel.kind`)
- `capture.classify` (with attribute `classification.label`, `classification.confidence`)
- `compliance.check_submission`
- `action.draft`, `action.execute`, `action.dismiss`
- `codegen.generate_module`
- `consolidation.run`
- `project.close`

---

## 10. Security model

| Concern | Mitigation |
|---|---|
| WorkIQ called as agent identity | Architecturally impossible: WorkIQ wrapper signature requires an `obo_context`. No callable path exists without one. Enforced in CI by a grep test. |
| Outbound spam from agent | All outbound goes through `actions.execute_suggested`, which requires a `coordinator_obo` argument; the message is sent *as* the coordinator. |
| Double-execution on retry | `executed_action_ids` set in `state.json`; checked before the side-effect call. |
| Charter tampering | Only `charter/ratify.py` and `charter/amend.py` write `charter.json`; both require coordinator OBO + ratification step; enforced by CI grep + filesystem mode (RO for everyone else inside the agent process). |
| Cross-project data leakage | `x-ms-chat-isolation-key = project_id`. Foundry guarantees per-session microVM `$HOME` isolation. |
| Owner viewing another owner's content | Each user's `render_dashboard` runs in *their* OBO; WorkIQ honours their M365 permissions. The asymmetry is the feature (see [spec §10.2](../functional-specs/project_workspace_spec.md)). |
| Long-lived secrets in the sandbox | Avoided. `GITHUB_TOKEN` is injected once at container start from Key Vault; WorkIQ tokens are short-lived per-call. Boot-time assertion refuses to start if `AZURE_AI_MODEL_DEPLOYMENT_NAME` is set (would silently flip the Copilot SDK off GHCP). |
| Stored M365 content residency | Only **summaries** persist in `state.json`. Raw extracted content is re-fetched on demand (see [spec §10.18](../functional-specs/project_workspace_spec.md)). |
| Conditional Access surprises | Frontend uses MSAL with interactive fallback; agent surfaces CA-block errors as a dedicated exception kind. |

---

## 11. Dependency manifest (target)

Lock these in `agent/pyproject.toml` once chosen; the table below is the intent.

| Purpose | Package | Notes |
|---|---|---|
| Invocations protocol server | `azure-ai-agentserver-invocations` | Serves `POST /invocations`; emits OpenTelemetry traces automatically; auto-injected App Insights connection string. **No Microsoft Agent Framework wrapper** — the Copilot SDK is the runtime directly on top of this. |
| GHCP Copilot SDK | `github-copilot-sdk` | **In-process** (`from copilot import CopilotClient`), authenticated via `GITHUB_TOKEN`. Single warm instance owned by `copilot_runtime.py`. |
| Foundry Agent Service client | `azure-ai-projects` | Only for any future portal-side metadata calls; the Toolbox is consumed directly via HTTP+MCP, not through this client. |
| Identity | `azure-identity` | `DefaultAzureCredential` for the Toolbox MCP endpoint and any opt-in `gpt-5.x` named-tool calls. The Copilot SDK does **not** use this. |
| MCP transport | `httpx` (manual JSON-RPC) | The Toolbox MCP client is implemented directly per [samplecode_toolbox.py](samplecode_toolbox.py); no shared MCP client library required. |
| Models | `pydantic` v2 | Charter, state, action, event |
| Schema enforcement | `import-linter`, `ruff`, `pyright` | CI gate. Enforces that only `copilot_runtime.py` imports `copilot.CopilotClient`. |
| Word/Excel handling | `python-docx`, `openpyxl` | Used inside the generated `consolidator.py`, but pinned at the agent level so Copilot doesn't have to invent dependencies. |
| Observability | `opentelemetry-api`, `opentelemetry-sdk`, `azure-monitor-opentelemetry-distro` | The Invocations protocol library wires the root spans; this layer adds children. |
| Testing | `pytest`, `pytest-asyncio`, `respx` (for HTTP mocks), `freezegun` | |

Frontend BFF, in `frontend/backend/pyproject.toml`:

| Purpose | Package |
|---|---|
| Web framework | `fastapi`, `uvicorn` |
| Auth | `msal` (BFF flow), `itsdangerous` (session cookies) |
| Foundry call | `httpx` |

Frontend SPA, in `frontend/ui/package.json`:

| Purpose | Package |
|---|---|
| Framework | `react` 18, `react-dom` 18 |
| Language | `typescript` 5 |
| Build | `vite` 5 |
| Routing | `react-router-dom` 6 (only for `/p/{project_id}` + a `/closed` view) |
| Data fetching | native `fetch` wrapped in a thin `bff.ts` client; **no** TanStack Query / SWR in MVP |
| Styling | CSS Modules; **no** Tailwind / CSS-in-JS in MVP |
| Lint/format | `eslint`, `prettier` |

The SPA is intentionally minimal: ~10 components (dashboard shell, task tile, exceptions panel, suggested-action card, chat input, charter ratification view, amendment dialog, close confirmation, refresh-state indicator, error banner). No state management library — `useState` + lifting state to the dashboard shell is enough for this surface. If the component count exceeds ~30, revisit.

---

## 12. Test strategy

| Layer | What we test | How |
|---|---|---|
| `state.py` | Atomicity, schema round-trip, append-only-ness of `activity.json` | unit tests + property tests (`hypothesis` if needed) |
| `workiq/` | Each wrapper produces the expected MCP call, propagates OBO, parses the response | `respx` mocking the MCP HTTP transport |
| `workiq/mcp_bridge.py` | Tool-name sanitisation (`.`/`-` → `_`); bridge round-trips a Copilot tool call back to MCP with the correct OBO context | unit tests with mocked Copilot tool-call envelope |
| `capture/handlers/*` | Cursor-correctness (no missed/duplicated events across two polls), filter correctness | fixture-mocked WorkIQ responses |
| `capture/` skill-driven classifier | Stable behaviour on a fixture set of 30+ events (board-pack scenario), each labelled | golden-file tests against a recorded Copilot session response (`RUN_COPILOT_TESTS` gate) |
| `status/triangulate.py` | Truth table from [spec §8.3](../functional-specs/project_workspace_spec.md) | parameterised tests |
| `actions/` | Double-execute is no-op; dismissed action cannot be re-approved; only coordinator OBO accepted | unit tests |
| `codegen/` | Generated `consolidator.py` passes the smoke fixture; failed generation retried exactly once then surfaces an exception | unit tests gated by `RUN_CODEGEN_TESTS` env (uses a real Copilot session) |
| `copilot_runtime.py` | Boot-time env-var assertions; warm-client reuse across `get_or_create_session` calls within a Foundry session | unit tests with patched env |
| `charter/` | Ratification rejects invalid Charters; amendment increments version; orphan-dependency check works | unit tests |
| End-to-end (Phase 4+) | Bundled sample meeting-notes flow: kickoff → simulated deliveries on each channel → consolidation → close | integration test against a dedicated test M365 tenant + a dev Foundry project |

---

## 13. Phasing map (spec §9 → modules)

| Phase | Modules in scope | Verb(s) exercised | Demoable outcome |
|---|---|---|---|
| 1. Skeleton | `__main__`, `copilot_runtime` (warm-only), `orchestrator`, `state` (counter only), frontend skeleton | echo verb | Two browsers, same project ID, same counter; same Copilot session resumed across requests |
| 2. Charter & kickoff | `charter/`, `kickoff/`, `workiq/` (Mail, Files, Teams, Tasks), initial skills (`project-kickoff`) | `propose_charter`, `ratify_charter` | Real M365 fan-out for the bundled sample scenario |
| 3. Skills + exceptional codegen | `agent/skills/*` (status-refresh, capture-classify, compliance-check, render-dashboard, draft-outbound, consolidate), `codegen/` (consolidator only), `consolidation/` (stub call) | (used by Phase 2 path) | Skills auto-loaded; show generated `consolidator.py` in the sandbox |
| 4. Capture loop | `capture/`, `status/`, `actions/` (draft only via `draft-outbound` skill) | `render_dashboard` | Live status changes as files/messages change |
| 5. Dashboard + approvals | Frontend SPA + exceptions panel, `actions/.execute` | `execute_suggested`, `coordinator_chat` | Approve a real Teams nudge sent as the coordinator |
| 6. Consolidation + closure | `consolidation/`, `charter/amend`, close path | `amend_charter`, `close_project` | Cross-section reconciliation finding fires; project closes; deliverable on SharePoint |
| 7. Hardening | All; add idempotency tests, CA edge cases, audit-log review | — | Production-ready posture |

---

## 14. Open questions to resolve during Phase 1

These should be turned into ADRs (one short MD per decision in `architecture/decisions/`) before Phase 2 starts.

1. **WorkIQ Toolbox endpoint URL pinning**: whether to pin to a specific Toolbox version (`/versions/{v}/mcp`) in dev and use the consumer endpoint (`/mcp`) in prod, or always use consumer. Default: pin in dev, consumer in prod.
2. **MCP approval policy for WorkIQ Toolbox tools**: `require_approval` choices per tool. Default proposal: `never` for read-only Mail/Calendar/Files/Teams; `always` for Mail/Teams *send* tools — though §10 already routes sends through the human-approval `actions/` layer, so `never` on send tools is acceptable if and only if the only caller path is `actions.execute_suggested`.
3. **BFF session strategy**: signed cookies vs Redis. Default: signed cookies; revisit if multi-instance scaling reveals a concern.
4. **Project-ID slug generation**: deterministic-from-goal vs prompt-the-coordinator. Default: propose a slug, let coordinator override at ratification.
5. **OOO detection**: how aggressive to be (mailbox auto-reply only? Calendar status too? Out-of-office banner in Teams?). Default: Calendar `showAs=oof` plus mailbox auto-reply; treat either as OOO.

---

## 15. Glossary

| Term | Meaning here |
|---|---|
| **Charter** | The per-project JSON contract in `$HOME/charter.json`. Project's constitution. |
| **Coordinator** | The human owner; ratifies, approves, closes. UPN held in `charter.stakeholders.coordinator`. |
| **Owner** | The human responsible for one `Task`. |
| **Observer** | Anyone with dashboard access who isn't producing content. |
| **Capture loop** | The on-visit cycle that polls channels, classifies events, updates status, drafts actions, renders. |
| **Suggested action** | A drafted outbound side-effect awaiting coordinator approval. |
| **OBO** | On-Behalf-Of token; the visiting user's delegated credential, used for every WorkIQ call and for executing approved actions. |
| **Toolbox** | A Foundry resource that bundles multiple MCP-compatible tools (e.g. all WorkIQ servers) behind a single MCP endpoint. See [references.md §8](../functional-specs/references.md). |
| **Codegen** | GHCP Copilot SDK writing `consolidator.py` into `$HOME/code/` at kickoff and on amendment when deterministic Python is genuinely needed. Renderer and compliance behaviour are skills, not generated modules. |
