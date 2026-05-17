# Architecture & Design — charter-agent

> The implementation-level companion to [`../functional-specs/project_workspace_spec.md`](../functional-specs/project_workspace_spec.md). The requirement spec answers *what* and *why*. This document answers *how* — concrete components, contracts, sequences, schemas, and the seams where the host runtime, the codegen sub-agent, and the Toolbox plug into otherwise generic agent code.
>
> Read [`../AGENTS.md`](../AGENTS.md) first for the non-negotiable invariants this design has been shaped around — in particular invariant 12 (dual-runtime: MAF `Agent` host + Copilot SDK codegen sub-agent) and invariant 3 (WorkIQ runs in the coordinator's OBO context, with deputy fallback).

**Status**: draft v0.2 — pre-implementation. Reflects the dual-runtime architecture decision. Expect revisions during Phase 1–3 (and the Phase 1.5 smoke gate) as the Foundry SDK behaviour gets pinned down against real responses.

---

## 1. Design principles, restated as decisions

These are the principles from the requirement spec, expressed as the design decisions they directly imply. Every later section is a consequence of these.

| Principle (from spec) | Concrete design decision |
|---|---|
| **Skills-first, agentskills.io-conformant** | All reusable agent capabilities (classify, draft, validate, consolidate, propose, render) are packaged as **Agent Skills** under `agent/skills/{name}/`, each containing a `SKILL.md` valid per the open [agentskills.io spec](https://agentskills.io/specification) — required YAML `name`/`description`, optional `metadata`/`license`/`compatibility`/`allowed-tools`, optional `scripts/`/`references/`/`assets/` subdirs, progressive disclosure (discovery → activation → execution). A small in-repo loader (`runtime/skill_loader.py`, ~50 lines) reads every `agent/skills/*/SKILL.md`, validates the frontmatter, and injects the body into the host MAF `Agent` at boot. See [AGENTS.md §4.3](../AGENTS.md#43-agent-skills-format-agentskillsio-conformance) for the format contract and [§4.4](../AGENTS.md#44-core-code-vs-skill--the-decision-rule) for the core-vs-skill decision rule. |
| Generic over project-specific | Three layers of variability: **Charter (data)** → **Agent Skills (declarative behaviour)** → **optional Copilot-generated `consolidator.py` (deterministic code, exceptional)** → **generic agent (constant)**. Nothing else varies per project. |
| WorkIQ runs in the coordinator's OBO context | All WorkIQ calls funnel through one wrapper module (`workiq/`) that obtains its token from `runtime/workiq_token.py`, which performs OBO on the coordinator's stored refresh token (deputy UPN as fallback per [AGENTS.md invariant 3](../AGENTS.md#3-non-negotiable-architectural-invariants)). The visiting user's identity is **not** propagated to WorkIQ; it is used only for dashboard authorisation and role filtering. |
| No background workers | The agent has *one* entry point — `handle_invocation(action, payload, visitor_identity)`. Every behaviour, including dashboard refresh, is reached from there. |
| State lives in `$HOME` | A single `state.py` module is the only place that touches files in `$HOME`. Atomic write-via-temp-then-rename for every mutation. The MAF `AgentSession` thread persists to `$HOME/agent_session/` and is resumed across `/invocations` calls. |
| Charter immutability | `charter.json` is read by everything; written only by `charter/ratify.py` and `charter/amend.py`. Enforced by a module-level lock plus a CI test that greps for forbidden writers. |
| Human-in-the-loop outbound | A `SuggestedAction` is the only path to an outbound side-effect. Two functions: `draft(...)` (returns a `SuggestedAction`, writes to state) and `execute(action_id)` (idempotent; obtains the coordinator OBO token from `runtime/workiq_token.py`). |
| Channel extensibility | Channel handlers register with `@register_channel("sharepoint_file")` decorators; the capture loop iterates the registry; new channels are additive. |
| Dual runtime, sharply split | Two runtimes by purpose: (a) **host** — one warm MAF `Agent` per process on a Foundry `gpt-5.x` deployment via Managed Identity, owned by `runtime/foundry_host.py`, owns `/invocations`, `AgentSession` thread, skills, native `MCPStreamableHTTPTool` dispatch, every everyday reasoning verb; (b) **codegen sub-agent** — one warm `CopilotClient`-backed `GitHubCopilotAgent` (PAT passed via constructor), owned by `runtime/copilot_codegen.py`, callable only from `codegen/`, used only to generate `$HOME/code/consolidator.py`. No third LLM path. See [AGENTS.md §3 invariant 12](../AGENTS.md#3-non-negotiable-architectural-invariants) and [§4.2](../AGENTS.md#42-model-assignment-policy). |

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
│                  passes as x-agent-chat-isolation-key,             │          │
│                  forwards visitor bearer token (auth/role only;   │          │
│                  NOT propagated to WorkIQ — see invariant 3)       │          │
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
│                              │  Foundry Toolbox        │   │ HOST RUNTIME   ││
│                              │  (MCP endpoint that     │   │ (MAF Agent     ││
│                              │   bundles all WorkIQ    │   │  on Foundry    ││
│                              │   MCP servers) —        │   │  gpt-5.x via   ││
│                              │   declared as native    │   │  Managed Ident.││
│                              │   MAF MCPStreamable-    │   │  AgentSession  ││
│                              │   HTTPTool on the host  │   │  resumed via   ││
│                              │   Agent.                │   │                ││
│                              │                         │   │  FOUNDRY_      ││
│                              │   • Mail                │   │  AGENT_SESSION_││
│                              │   • Calendar            │   │  ID. Runs all  ││
│                              │   • Files (SP/OneDrive) │   │  everyday      ││
│                              │   • Teams               │   │  reasoning.    ││
│                              │   • workiq.ask          │   └────────────────┘│
│                              │                         │   ┌────────────────┐│
│                              │  Auth on each call:     │   │ CODEGEN SUB-   ││
│                              │  coordinator OBO        │   │ AGENT          ││
│                              │  (deputy fallback)      │   │ (CopilotClient ││
│                              │  stamped by             │   │  + GHCP, PAT   ││
│                              │  workiq_token.py        │   │  via constr.   ││
│                              │                         │   │  Used ONLY by  ││
│                              │                         │   │  codegen/ to   ││
│                              │                         │   │  write         ││
│                              │                         │   │  consolidator. ││
│                              │                         │   │  py.)          ││
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
| `__main__.py` | Wire `azure-ai-agentserver-invocations` to `orchestrator.handle_invocation`. Warm both runtimes via `runtime.foundry_host.bootstrap()` and `runtime.copilot_codegen.bootstrap()`. Load skills via `runtime.skill_loader.load_all()`. Assert env-var policy (**both** `AZURE_AI_MODEL_DEPLOYMENT_NAME` and `GITHUB_TOKEN` present; `TOOLBOX_NAME` present; coordinator OBO confidential-client creds present). Nothing else. | `azure.ai.agentserver.invocations`, `runtime.*`, `orchestrator` |
| `runtime/foundry_host.py` | **Sole owner of the host MAF `Agent`.** One warm instance per process on the Foundry `gpt-5.x` deployment via `DefaultAzureCredential` (Managed Identity). One MAF `AgentSession` thread per Foundry session, keyed by `FOUNDRY_AGENT_SESSION_ID`, persisted to `$HOME/agent_session/`. Declares the native MAF `MCPStreamableHTTPTool` against the Toolbox endpoint (`approval_mode="never_require"`, `load_prompts=False`, mandatory `Foundry-Features: Toolboxes=V1Preview` header + bearer stamped via `httpx` event hook, per-call coordinator-token header injector). Exposes `run_skill(name, **inputs)` to the rest of the agent. Enforced by `import-linter` as the only instantiator of `Agent` and `FoundryChatClient`. | `agent_framework`, `agent_framework_foundry`, `azure_identity`, `runtime.workiq_token`, `runtime.skill_loader` |
| `runtime/copilot_codegen.py` | **Sole owner of `CopilotClient`.** One warm `CopilotClient`-backed `GitHubCopilotAgent` (via `CopilotClient.AsAIAgent()`) per process. PAT read from `GITHUB_TOKEN` and passed **as a constructor argument** to defeat the silent Foundry-backend flip when `AZURE_AI_MODEL_DEPLOYMENT_NAME` is also in the env (see [AGENTS.md §4.2](../AGENTS.md#42-model-assignment-policy)). Exposes a single tool-shaped surface: `generate_python_module(prompt, staging_path)`. Enforced by `import-linter` as the only instantiator of `CopilotClient` and as callable **only** from `codegen/`. | `github_copilot_sdk` (`copilot`), `agent_framework` |
| `runtime/skill_loader.py` | Reads each `agent/skills/*/SKILL.md`, validates the agentskills.io YAML frontmatter (`name` matches parent dir, `description` length, etc.), and registers each skill body with the host `Agent` so the host model can select among them. ~50 lines. | `pyyaml`, `runtime.foundry_host` |
| `runtime/workiq_token.py` | Sole owner of WorkIQ token acquisition. Performs confidential-client OBO on the coordinator's stored refresh token (`COORDINATOR_OBO_TENANT_ID/CLIENT_ID/CLIENT_SECRET`). On failure (PTO, revoked token, Conditional Access block) falls back silently to the deputy UPN from the Charter. Visitor identity is **not** an input. Refresh handled in-band. | `msal`, `azure_identity`, `state` (for cached refresh tokens) |
| `orchestrator.py` | One function per action verb (§7). Translates the verb into a skill invocation on the host `Agent` and/or a direct call into a domain module; never implements reasoning itself. | All of the below |
| `charter/` | Pydantic schema, JSON Schema export, ratification flow, amendment flow. **Sole writer of `charter.json`**. | `state`, `workiq` (for grounding), `codegen` (regen `consolidator.py` on amend) |
| `kickoff/` | Fan-out actions on a freshly ratified Charter: SharePoint folder + templated files, Teams kickoff message, Outlook tasks, briefing emails. All issued in the coordinator's OBO context. | `workiq`, `actions`, `state` |
| `capture/` | Channel-handler registry + handlers (one file per `channel.kind`). The classifier itself is a **skill** (`agent/skills/capture-classify/`), invoked via `runtime.foundry_host.run_skill(...)`. | `workiq`, `state`, `runtime.foundry_host` |
| `status/` | Pure functions: given submissions + channel signals + calendar + Charter, return per-task status. **No side effects.** | (stdlib only) |
| `actions/` | `SuggestedAction` lifecycle: draft (via the `draft-outbound` skill on the host `Agent`), persist, execute, mark-executed. Idempotency lives here. Outbound execution always uses the coordinator OBO token obtained from `runtime.workiq_token`. | `workiq`, `state`, `runtime.foundry_host` |
| `codegen/` | **Exceptional** path: generates `$HOME/code/consolidator.py` at kickoff and on amendment, by calling `runtime.copilot_codegen.generate_python_module(...)`. The **only** module allowed to import `runtime.copilot_codegen`. | `runtime.copilot_codegen`, `state` |
| `consolidation/` | Loads the generated `consolidator.py`, runs it, surfaces findings. | `state`, generated module |
| `state.py` | Atomic read/write of every `$HOME` file (Charter, state, activity NDJSON, MAF `AgentSession` thread). Returns Pydantic models. | (stdlib only) |
| `workiq/` | Thin async wrappers around the Toolbox MCP tools. Two consumers: (a) MAF's native `MCPTool` (declared in `runtime/foundry_host.py`) for in-skill tool calls; (b) thin direct-call helpers for code paths that need raw tool output (kickoff fan-out, channel polling). Every wrapper obtains its token via `runtime.workiq_token.get_coordinator_token()` and **never** takes a visitor token. | `httpx`, `runtime.workiq_token`, `azure_identity` |
| `observability.py` | Re-exports `@trace_function` from `azure.ai.projects.telemetry` for custom spans, exposes `ProcessAttributesSpanProcessor` (stamps `project.id` / `gen_ai.conversation.id` on every span via `on_start`), and owns `log_activity(...)` for the `$HOME/activity.json` audit-log line. The Invocations protocol library and the Foundry `AIProjectInstrumentor` emit root + intermediate spans automatically; we do not hand-roll context managers or wire exporters. | `azure-ai-projects`, `opentelemetry`, `state` |

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
     │                            │  visitor bearer token)│                         │                        │
     │                            │ ─────────────────────►│                          │                        │
     │                            │                      │ ground prompt: workiq.ask│                        │
     │                            │                      │ for open-ended discovery │                        │
     │                            │                      │ (triggering email/mtg,   │                        │
     │                            │                      │ similar prior artifact,  │                        │
     │                            │                      │ runbook — any mix), then │                        │
     │                            │                      │ drill in with typed tools│                        │
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
     │                            │                      │ ────────────────────────►│ (coordinator OBO)      │
     │                            │ kickoff complete ◄───│                          │                        │
     │ dashboard ◄────────────────│                      │                          │                        │
```

### 4.2 Dashboard refresh (action verb: `render_dashboard`)

This is the **autonomous capture loop** ([spec §8](../functional-specs/project_workspace_spec.md)). It runs on every visit. For an observer's visit, the dashboard payload is filtered narrower; for the coordinator's visit, the suggested-action drafter is engaged. Every WorkIQ call inside the loop runs in the **coordinator's** OBO context (visitor identity is used only for dashboard auth and role filtering — see [AGENTS.md invariant 3](../AGENTS.md#3-non-negotiable-architectural-invariants)).

```
1. load charter.json, state.json
2. identify visitor from bearer token (for auth + role filter only)
3. obtain coordinator OBO token from runtime.workiq_token.get_coordinator_token() (deputy fallback)
4. for each task:
     for each watch_channel:
         handler = registry[channel.kind]
         events = handler.poll(charter, task, channel, since=state.last_check[task_id])  # uses coordinator token internally
         for event in events:
             # classification via the `capture-classify` skill on the host Agent
             classification = foundry_host.run_skill("capture-classify", event=event, task=task, charter=charter)
             match classification:
                 case submission | revised_submission:
                     content = workiq.extract(event)
                     # compliance check via the `compliance-check` skill (no generated .py module)
                     results = foundry_host.run_skill(
                         "compliance-check", submission=content, requirements=task.runbook_requirements,
                     )
                     state.record_submission(task_id, event, content, results)
                 case question:
                     state.add_exception(...)
                 case supporting | unrelated:
                     state.record_seen(event)
     state.last_check[task_id] = now
     status = status.triangulate(state.submissions[task_id], state.channel_signals[task_id], workiq.calendar(owner))
     state.set_status(task_id, status)
5. consolidation gate:
     if all(task.status in (Submitted, SubmittedWithGaps) for task in charter.tasks):
         findings = generated_consolidator.consolidate(charter, state)
         state.record_consolidation(findings)
6. if visitor == coordinator:
     for task in charter.tasks:
         maybe_draft_action(task, state)   # via the `draft-outbound` skill
7. payload = foundry_host.run_skill("render-dashboard", charter=charter, state=state, viewer=visitor)
8. return payload
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
      4. token = runtime.workiq_token.get_coordinator_token()  # deputy fallback applies
      5. workiq.send_teams_message(action.payload)              # internally stamps coordinator token
      6. state.executed_action_ids.add(approve_action_id)
      7. state.suggested_actions[approve_action_id].status = "executed"
      8. log activity (with coordinator identity, action UUID, target)
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

Only **one** module is generated under the skills-first runtime: `$HOME/code/consolidator.py`. The renderer and compliance behaviours from the v0 design are now skills (`agent/skills/render-dashboard/`, `agent/skills/compliance-check/`) and produce their output directly from the host `Agent`. Generate a Python module only when the work needs deterministic running code (template-specific Word/Excel stitching, cross-section numeric reconciliation).

```python
# consolidator.py
def consolidate(charter: dict, state: dict) -> dict:
    """Returns {'output_path': str, 'reconciliation_findings': [ ... ]}."""
```

After codegen, the module is **import-tested** before being committed to `$HOME`. The codegen wrapper (in `codegen/`):

1. Calls `runtime.copilot_codegen.generate_python_module(prompt, staging_path=$HOME/code/_staging/consolidator.py)`. This is the only way into the codegen sub-agent.
2. Imports the staged file in an isolated subinterpreter.
3. Calls a smoke-test fixture against the function signature.
4. On success, moves to `$HOME/code/consolidator.py` atomically.
5. On failure, retries the codegen prompt with the error appended once; on second failure, raises and surfaces the codegen failure as an exception in `state.exceptions`.

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

The classifier is **an agentskills.io skill** (`agent/skills/capture-classify/SKILL.md`), not a generated module and not a separate LLM call path. The capture loop invokes it via `runtime.foundry_host.run_skill("capture-classify", ...)`, which feeds the skill's instructions plus the inputs below into the host `Agent` and parses a structured response.

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

Authorisation: the agent extracts the visitor's UPN from the bearer token on the request and compares it to `charter.stakeholders.coordinator` to gate coordinator-only verbs. The frontend additionally hides those controls for non-coordinators, but the agent is the authority. The visitor token is **not** used to call WorkIQ — see [AGENTS.md invariant 3](../AGENTS.md#3-non-negotiable-architectural-invariants) and [§8.2](#82-host-runtime-codegen-sub-agent-and-the-toolbox-mcptool) below.

---

## 8. Runtime architecture: MAF host + Copilot codegen sub-agent + Toolbox MCPTool

Under the dual-runtime architecture ([AGENTS.md invariant 12](../AGENTS.md#3-non-negotiable-architectural-invariants)) the agent process contains **two MAF agents** inside one `azure-ai-agentserver-invocations` server: the host `Agent` for everyday reasoning, and the codegen `GitHubCopilotAgent` for `consolidator.py` generation. Each has a dedicated `runtime/*.py` owner; nothing else in the codebase may construct these clients.

### 8.1 Foundry Toolbox via native MAF `MCPTool`

The Toolbox is consumed by the host `Agent` through MAF's first-class `MCPStreamableHTTPTool`. There is no hand-rolled bridge in the production code path; `architecture/samplecode_toolbox.py` (portal-generated) remains in the repo as a wire-shape debugging aid only — its hand-rolled `McpBridge` (`initialize` / `mcp-session-id` / `notifications/initialized` / streamed `tools/call` / Copilot-SDK tool-name sanitisation `.`/`-` → `_`) is exactly what `MCPStreamableHTTPTool` replaces.

Wiring requirements — every row is a hard requirement of the Toolbox MCP endpoint as it stands today:

| Concern | Detail | Where it lives in our code |
|---|---|---|
| Endpoint URL | `{project_endpoint}/toolboxes/Charter-Agent-Tools/versions/{ver}/mcp?api-version=v1` in dev; `/toolboxes/Charter-Agent-Tools/mcp?api-version=v1` (consumer) in prod | env var `TOOLBOX_MCP_ENDPOINT` (**not** `FOUNDRY_TOOLBOX_ENDPOINT` — the `FOUNDRY_` prefix is reserved by the platform) |
| Server label | Stable `"workiq"` across Toolbox version switches | `runtime/foundry_host.py` |
| MAF → Toolbox auth | `DefaultAzureCredential` against the `https://ai.azure.com/.default` scope (Foundry-assigned Managed Identity) | `runtime/foundry_host.py` |
| Mandatory header | `Foundry-Features: Toolboxes=V1Preview` on every request — wired via `MCPTool`'s headers option | `runtime/foundry_host.py` |
| Per-call `Authorization` | Header-injector callback obtains the **coordinator's** WorkIQ delegated token from `runtime/workiq_token.py` (deputy fallback on failure) and stamps it on each outbound MCP request. Visitor identity is **not** propagated. | `runtime/foundry_host.py` + `runtime/workiq_token.py` |
| Approval policy | `MCPTool(require_approval="never")` for WorkIQ reads **and** writes. Approval is enforced at a higher layer (the `actions/` module's `SuggestedAction` lifecycle, gated by the coordinator in the dashboard) | `runtime/foundry_host.py` and `actions/` |
| MCP protocol plumbing | MAF handles `initialize`, `mcp-session-id`, `notifications/initialized`, `tools/list` (cached), `tools/call` (streamed where supported), approval-item items | MAF internals |
| Capture-loop concurrency | Fan out channel polls concurrently (`asyncio.gather`) — the 100-second non-streaming MCP-call timeout is per-call, not per-batch | `capture/handlers/*` |

**Standing rule when extending beyond what's documented.** For anything not directly handled by `MCPTool` — a new Toolbox tool, a different MCP method, a changed input schema, a new auth header, a bumped MCP `protocolVersion`, a new server capability — introspect the **live Toolbox endpoint** (`tools/list`, `initialize` capabilities) rather than coding against a stale snapshot. Reference the Microsoft Learn docs cited in [references.md §8](../functional-specs/references.md) and [§3 — `MCPTool`](../functional-specs/references.md). If the portal regenerates `samplecode_toolbox.py`, replace it and update this section in the same PR.

### 8.2 Host runtime, codegen sub-agent, and the Toolbox MCPTool

Three owners, enforced by `import-linter`:

- **`runtime/foundry_host.py`** is the **only** module allowed to instantiate the host `Agent`. It exposes three responsibilities to the rest of the agent: (a) the warm singleton `Agent` (one per process), (b) per-Foundry-session `AgentSession` thread resume keyed by `FOUNDRY_AGENT_SESSION_ID` and persisted to `$HOME/agent_session/`, (c) the `run_skill(name, **inputs)` helper that the orchestrator / capture / actions layers use for skill-driven reasoning. It also declares the native MAF `MCPStreamableHTTPTool` against the Toolbox endpoint, with the header injector that stamps the coordinator's WorkIQ token on every call.

- **`runtime/copilot_codegen.py`** is the **only** module allowed to instantiate `CopilotClient` (and to wrap it via `CopilotClient.AsAIAgent()` / `GitHubCopilotAgent`). It reads `GITHUB_TOKEN` from the OS env and passes it to `CopilotClient(github_token=…)` **as a constructor argument** — not via env propagation — to defeat the SDK's silent Foundry-backend flip when `AZURE_AI_MODEL_DEPLOYMENT_NAME` is also present (see [AGENTS.md §4.2](../AGENTS.md#42-model-assignment-policy) and [references.md §9](../functional-specs/references.md)). It exposes one surface to the rest of the codebase: `generate_python_module(prompt: str, staging_path: Path) → Path`. It is **not** on any everyday-reasoning path.

- **`codegen/`** is the **only** caller of `runtime/copilot_codegen.py`. Today it generates a single module — `consolidator.py` — and only when consolidation logic genuinely needs deterministic Python (template-specific Word stitching, cross-section numeric reconciliation).

```python
# codegen/generate.py
async def generate_module(module_name: Literal["consolidator"], charter: Charter) -> Path:
    prompt = PROMPTS[module_name].render(charter=charter)
    staging = HOME / "code" / "_staging" / f"{module_name}.py"
    staging.parent.mkdir(parents=True, exist_ok=True)
    await runtime.copilot_codegen.generate_python_module(
        prompt=f"{prompt}\n\nWrite the result to {staging}",
        staging_path=staging,
        timeout=180.0,
    )
    validate_module(staging, module_name)
    return move_atomically(staging, HOME / "code" / f"{module_name}.py")
```

Properties:

- **One warm client per runtime, for the lifetime of the process.** The host `Agent`'s thread is project-scoped (`FOUNDRY_AGENT_SESSION_ID`); the codegen sub-agent uses short-lived turns and does not share conversational state with the host.
- **Validation before promotion.** Import + signature check + smoke fixture; failure triggers one retry with error context appended to the prompt; second failure raises and surfaces as `state.exceptions`.
- **Telemetry.** Each generation emits an OTel span `codegen.generate_module` with attributes `module`, `charter_version`, `attempt`, `outcome`. The Invocations protocol library and MAF auto-emit parent / intermediate spans.
- **Source preserved.** The generated file is left in `$HOME/code/` with a header comment `# auto-generated for charter v{n} at {ts}`. The activity log records the path; an auditor can read the actual file later.
- **Credential isolation, verified.** A test in the test matrix asserts that the codegen sub-agent actually reaches GHCP (not the Foundry backend) — verifiable via OTel attribute / token-issuer header on the outbound call. This is the Phase 1.5 smoke gate.

Prompt templates are versioned alongside the code; changing a prompt is a code change, reviewed like any other.

---

## 9. Observability & audit

Two layers, decoupled by ownership:

1. **App Insights / OTel spans** — owned by Foundry. Server-side (root invocation, model call, tool call) is auto-emitted by the platform once the project is connected to App Insights. Client-side (per skill / per workflow step) is auto-emitted by `AIProjectInstrumentor().instrument()` plus any function decorated with `@trace_function` (re-exported from `observability`). Process-wide attributes (`project.id`, `gen_ai.conversation.id`) are stamped by `ProcessAttributesSpanProcessor.on_start`, registered once at boot. No hand-rolled span context managers, no manual exporter wiring.
2. **`$HOME/activity.json` entry** — owned by us, written via `observability.log_activity(...)`. This is the project's narrative audit trail, exportable to SharePoint at close. It is product behaviour, not telemetry, and is decoupled from the OTel pipeline.

Standard span attributes for every span:

| Attribute | Source |
|---|---|
| `project_id` | from chat-isolation key |
| `actor.upn` | from OBO token; `system` for agent-internal spans |
| `actor.role` | `coordinator` / `owner` / `observer` / `system` |
| `charter.version` | from `charter.json` |
| `verb` | the action being handled, if applicable |

Recommended custom span names (non-exhaustive) — apply via `@trace_function("<name>")` on the relevant function; the root `/invocations` span and model/tool spans are emitted by the platform and the Foundry instrumentor and should not be re-created:

- `charter.invocation` (top dispatcher), `charter.propose`, `charter.ratify`, `charter.amend`
- `kickoff.fanout`, `kickoff.create_templated_file`, `kickoff.post_teams`, `kickoff.send_email`, `kickoff.create_task`
- `capture.poll_channel` (function arg `channel_kind` shows up as `code.function.parameter.channel_kind`)
- `capture.classify` (return value carries `classification.label` / `classification.confidence` as part of `code.function.return.value`)
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
| Charter tampering | Only `charter/ratify.py` and `charter/amend.py` write `charter.json`; both run through the coordinator-ratification flow; enforced by CI grep + filesystem mode (RO for everyone else inside the agent process). |
| Cross-project data leakage | `x-agent-chat-isolation-key = project_id`. Foundry guarantees per-session microVM `$HOME` isolation. |
| Coordinator-only view of M365 | Every WorkIQ call inside the agent uses the **coordinator's** OBO context (deputy fallback per Charter), not the visitor's. Collaborators see the dashboard via SSO; per-role filtering is applied server-side in `render-dashboard`. This is what makes the shared-session model tractable — see [AGENTS.md invariant 3](../AGENTS.md#3-non-negotiable-architectural-invariants) and [spec §10.2](../functional-specs/project_workspace_spec.md). |
| Long-lived secrets in the sandbox | `GITHUB_TOKEN` (codegen sub-agent PAT) and `COORDINATOR_OBO_CLIENT_SECRET` are injected once at container start from Key Vault. WorkIQ tokens are short-lived per-call. Boot-time assertion refuses to start if **either** `AZURE_AI_MODEL_DEPLOYMENT_NAME` or `GITHUB_TOKEN` is missing; the codegen sub-agent passes the PAT to `CopilotClient(…)` via constructor to defeat the SDK's silent backend flip even though both env vars are deliberately present (see [AGENTS.md §4.2](../AGENTS.md#42-model-assignment-policy)). |
| Stored M365 content residency | Only **summaries** persist in `state.json`. Raw extracted content is re-fetched on demand (see [spec §10.18](../functional-specs/project_workspace_spec.md)). |
| Conditional Access surprises | Frontend uses MSAL with interactive fallback; agent surfaces CA-block errors as a dedicated exception kind. |

---

## 11. Dependency manifest (target)

Lock these in `agent/pyproject.toml` once chosen; the table below is the intent.

| Purpose | Package | Notes |
|---|---|---|
| Invocations protocol server | `azure-ai-agentserver-invocations` | Serves `POST /invocations`; emits OpenTelemetry traces automatically; auto-injected App Insights connection string. Both runtimes (host + codegen sub-agent) sit on top of this one server. |
| Host runtime | `agent-framework-core`, `agent-framework-foundry` | Microsoft Agent Framework. Owns the `Agent`, `AgentSession`, native `MCPStreamableHTTPTool`. Authenticated to the Foundry `gpt-5.x` deployment via `DefaultAzureCredential` (Managed Identity). Single warm instance owned by `runtime/foundry_host.py`. |
| Codegen sub-agent | `github-copilot-sdk` | **In-process** (`from copilot import CopilotClient`), wrapped as a MAF agent via `CopilotClient.AsAIAgent()` / `GitHubCopilotAgent`. PAT passed **via constructor** (not env) to defeat silent backend flip. Single warm instance owned by `runtime/copilot_codegen.py`. Called only from `codegen/`. |
| Foundry Agent Service client | `azure-ai-projects` | Only for any future portal-side metadata calls; the Toolbox is consumed via MAF's native `MCPTool`. |
| Identity | `azure-identity`, `msal` | `DefaultAzureCredential` for the host model + Toolbox; `msal` for coordinator OBO confidential-client flow inside `runtime/workiq_token.py`. |
| MCP transport | (none; provided by `agent-framework`) | MAF's `MCPTool` handles `initialize` / `mcp-session-id` / `tools/list` / `tools/call` / approval items. `httpx` is used only by the thin direct-call helpers in `workiq/` for non-MAF paths (kickoff fan-out, channel polling). |
| Models | `pydantic` v2 | Charter, state, action, event |
| Schema enforcement | `import-linter`, `ruff`, `pyright` | CI gate. Enforces that only `runtime/foundry_host.py` instantiates `Agent` (and `FoundryChatClient`); only `runtime/copilot_codegen.py` instantiates `CopilotClient`; only `codegen/` imports `runtime/copilot_codegen`. |
| Word/Excel handling | `python-docx`, `openpyxl` | Used inside the generated `consolidator.py`, pinned at the agent level so the codegen sub-agent cannot invent dependencies. |
| Observability | `azure-ai-projects>=2.0.0` (`AIProjectInstrumentor`, `@trace_function`), `azure-core-tracing-opentelemetry`, `azure-monitor-opentelemetry`, `opentelemetry-api`, `opentelemetry-sdk` | Foundry server-side traces are automatic once the project is connected to App Insights (preview for hosted agents). Client-side instrumentation is one call (`AIProjectInstrumentor().instrument()` + `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true`); custom spans use `@trace_function`. The App Insights connection string is auto-injected — do not call `configure_azure_monitor` again. `observability.py` adds the audit-log line and the process-attribute `SpanProcessor`. |
| Testing | `pytest`, `pytest-asyncio`, `respx` (HTTP mocks), `freezegun` | |

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
| `state.py` | Atomicity, schema round-trip, append-only-ness of `activity.json`, MAF `AgentSession` thread round-trip | unit tests + property tests (`hypothesis` if needed) |
| `workiq/` | Each wrapper produces the expected MCP call shape, obtains its token from `runtime/workiq_token.py`, parses the response | `respx` mocking the MCP HTTP transport |
| `runtime/workiq_token.py` | Coordinator OBO refresh; silent deputy fallback on coordinator-token failure; issued token carries correct claims (`upn`, `oid`) | unit tests with mocked MSAL backend |
| `runtime/foundry_host.py` | Warm `Agent` reuse within Foundry session; `MCPStreamableHTTPTool` header injector receives a fresh coordinator token on each call | unit tests with patched MAF + workiq_token |
| `runtime/copilot_codegen.py` | PAT passed via constructor (not env); only `codegen/` is allowed to import (import-linter); the codegen sub-agent really reaches GHCP, not the Foundry backend, even with `AZURE_AI_MODEL_DEPLOYMENT_NAME` set | unit tests + Phase 1.5 smoke against a real sandbox |
| `runtime/skill_loader.py` | Rejects invalid YAML frontmatter; `name` must equal parent dir; description length bounds | unit tests against fixture skill dirs |
| `capture/handlers/*` | Cursor-correctness (no missed/duplicated events across two polls), filter correctness | fixture-mocked WorkIQ responses |
| `capture/` skill-driven classifier | Stable behaviour on a fixture set of 30+ events (board-pack scenario), each labelled | golden-file tests against a recorded host-model response (`RUN_HOST_MODEL_TESTS` gate) |
| `status/triangulate.py` | Truth table from [spec §8.3](../functional-specs/project_workspace_spec.md) | parameterised tests |
| `actions/` | Double-execute is no-op; dismissed action cannot be re-approved; outbound side-effects always sourced from coordinator OBO | unit tests |
| `codegen/` | Generated `consolidator.py` passes the smoke fixture; failed generation retried exactly once then surfaces an exception; codegen call traces hit GHCP | unit tests gated by `RUN_CODEGEN_TESTS` (uses the real codegen sub-agent) |
| `charter/` | Ratification rejects invalid Charters; amendment increments version; orphan-dependency check works; deputy UPN required and resolvable | unit tests |
| Boot smoke | Refuses to start without **both** `AZURE_AI_MODEL_DEPLOYMENT_NAME` and `GITHUB_TOKEN`; both runtimes healthy on real sandbox; coordinator OBO yields a usable WorkIQ token | Phase 1.5 gate |
| End-to-end (Phase 4+) | Bundled sample meeting-notes flow: kickoff → simulated deliveries on each channel → consolidation → close | integration test against a dedicated test M365 tenant + a dev Foundry project |

---

## 13. Phasing map (spec §9 → modules)

| Phase | Modules in scope | Verb(s) exercised | Demoable outcome |
|---|---|---|---|
| 1. Skeleton | `__main__`, `runtime/foundry_host` (warm-only), `runtime/skill_loader` (load empty set), `orchestrator`, `state` (counter only), frontend skeleton | echo verb | Two browsers, same project ID, same counter; MAF `AgentSession` resumed across requests |
| **1.5. Dual-runtime smoke** — **gate** | `runtime/copilot_codegen` warmup; trivial `consolidate` skill asking the codegen sub-agent for a one-line Python file; coordinator OBO `runtime/workiq_token` exercised end-to-end against test tenant | (none new — internal smoke only) | Host runtime answers a chat turn on Foundry model; codegen sub-agent reaches GHCP (verified via OTel attribute / token-issuer header) and writes `$HOME/code/smoke.py`; coordinator OBO yields a usable WorkIQ token |
| 2. Charter & kickoff | `charter/`, `kickoff/`, `workiq/` (Mail, Files, Teams, Tasks), initial skills (`project-kickoff`) | `propose_charter`, `ratify_charter` | Real M365 fan-out for the bundled sample scenario |
| 3. Skills + exceptional codegen | `agent/skills/*` (status-refresh, capture-classify, compliance-check, render-dashboard, draft-outbound, consolidate), `codegen/` (consolidator only), `consolidation/` (stub call) | (used by Phase 2 path) | Skills loaded by `runtime/skill_loader`; generated `consolidator.py` visible in the sandbox |
| 4. Capture loop | `capture/`, `status/`, `actions/` (draft only via `draft-outbound` skill) | `render_dashboard` | Live status changes as files/messages change |
| 5. Dashboard + approvals | Frontend SPA + exceptions panel, `actions/.execute` | `execute_suggested`, `coordinator_chat` | Approve a real Teams nudge sent as the coordinator |
| 6. Consolidation + closure | `consolidation/`, `charter/amend`, close path | `amend_charter`, `close_project` | Cross-section reconciliation finding fires; project closes; deliverable on SharePoint |
| 7. Hardening | All; add idempotency tests, CA edge cases (deputy fallback), audit-log review | — | Production-ready posture |

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
| **OBO** | On-Behalf-Of token. In this project, the **coordinator's** delegated credential, obtained by `runtime/workiq_token.py` via confidential-client OBO and used for every WorkIQ call (read and write). On coordinator-token failure, falls back to the deputy UPN named in the Charter. The visitor's identity is *not* propagated to WorkIQ. See [AGENTS.md invariant 3](../AGENTS.md#3-non-negotiable-architectural-invariants). |
| **Host runtime** | The MAF `Agent` on a Foundry `gpt-5.x` deployment via Managed Identity. Owns `/invocations`, `AgentSession`, skills, `MCPStreamableHTTPTool` dispatch, every everyday reasoning verb. Sole owner: `runtime/foundry_host.py`. |
| **Codegen sub-agent** | A second MAF agent (`GitHubCopilotAgent`) wrapping a `CopilotClient` on GHCP's default model (Claude Opus 4.7). PAT passed via constructor. Used **only** by `codegen/` to generate `$HOME/code/consolidator.py`. Sole owner: `runtime/copilot_codegen.py`. |
| **Toolbox** | A Foundry resource that bundles multiple MCP-compatible tools (e.g. all WorkIQ servers) behind a single MCP endpoint. See [references.md §8](../functional-specs/references.md). |
| **Codegen** | The codegen sub-agent writing `consolidator.py` into `$HOME/code/` at kickoff and on amendment when deterministic Python is genuinely needed. Renderer and compliance behaviour are skills, not generated modules. |
