"use strict";

const $ = (id) => document.getElementById(id);
const transcript = $("transcript");
const dashboard = $("dashboard");
const promptEl = $("prompt");
const sendBtn = $("sendBtn");
const loginBtn = $("loginBtn");
const resetBtn = $("resetBtn");
const authPill = $("authPill");
const userPill = $("userPill");
const sessionMeta = $("sessionMeta");
const endpointMeta = $("endpointMeta");
const modeSelect = $("modeSelect");
const activityList = $("activity");
const logsList = $("logs");

let signedIn = false;
let currentUserUpn = null;
let currentUserOid = null;
let busy = false;
let currentAgentMsg = null;       // streaming agent bubble DOM node
let currentAgentBuffer = "";      // accumulated agent text for the in-flight turn
let activeTool = null;            // {node, startedAt, ticker} for the most recent tool.call
const shownPhaseCards = new Set();// phase-card keys already rendered this turn
let _currentTurnIsAuto = false;   // true when the running turn was triggered by AutoPoller
let _pollIntervalMins = 30;       // learned from ctx.poll_interval_mins at boot
let _nextCheckAt = 0;             // epoch ms when next auto-check fires; 0 = unknown
let _schedCountdownTimer = null;

const schedBar    = $("schedBar");
const schedStatus = $("schedStatus");

function setSchedStatus(state, text) {
  if (!schedBar) return;
  schedBar.className = "sched-bar" + (state ? " " + state : "");
  if (schedStatus) schedStatus.textContent = text;
}

function _tickSchedCountdown() {
  if (!_nextCheckAt || !schedBar) return;
  if (schedBar.classList.contains("checking")) return; // agent running — don't overwrite
  const msLeft = _nextCheckAt - Date.now();
  const wasUpdated = schedBar.classList.contains("updated");
  if (msLeft <= 0) {
    // Past due — Python poller is about to fire; show amber pending dot.
    setSchedStatus("pending", "Starting auto-check…");
  } else if (msLeft < 60_000) {
    // Final minute: count down in seconds.
    const secsLeft = Math.ceil(msLeft / 1_000);
    setSchedStatus(wasUpdated ? "updated" : "", `Auto-check in ${secsLeft}s…`);
  } else {
    const minsLeft = Math.round(msLeft / 60_000);
    setSchedStatus(wasUpdated ? "updated" : "", `Next auto-check in ${minsLeft} min`);
  }
}

function _armSchedCountdown(intervalMins) {
  if (intervalMins) _pollIntervalMins = intervalMins;
  _nextCheckAt = Date.now() + _pollIntervalMins * 60_000;
  if (_schedCountdownTimer) clearInterval(_schedCountdownTimer);
  // Tick every 10 s so pending/due transitions appear promptly.
  _schedCountdownTimer = setInterval(_tickSchedCountdown, 10_000);
  _tickSchedCountdown(); // show immediately
}

const toastHost = $("toastHost");

function showToast(title, body, { clickPid = null, hasUpdates = false } = {}) {
  if (!toastHost) return;
  const el = document.createElement("div");
  el.className = "toast" + (clickPid ? " clickable" : "") + (hasUpdates ? " has-updates" : "");
  el.innerHTML = `<div class="toast-title">${escapeHtml(title)}</div>
<div class="toast-body">${escapeHtml(body)}</div>
${clickPid ? '<div class="toast-hint">Click to switch to this project</div>' : ""}`;
  if (clickPid) {
    el.addEventListener("click", async () => {
      el.remove();
      const r = await window.pywebview.api.switch_project(clickPid);
      if (r && r.ok) { renderProjects(r); loadActiveProjectView(r); }
    });
  }
  toastHost.appendChild(el);
  // Auto-dismiss after 8 s (updates) or 5 s (no changes).
  setTimeout(() => el.remove(), hasUpdates ? 8000 : 5000);
}

function fmtRelTime(isoOrEpoch) {
  const d = typeof isoOrEpoch === "number"
    ? new Date(isoOrEpoch * 1000)
    : new Date(isoOrEpoch);
  const diffMs = Date.now() - d.getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins === 1) return "1 min ago";
  return `${mins} min ago`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]));
}

function addMsg(kind, who, text) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${kind}`;
  wrap.innerHTML = `<div class="who">${escapeHtml(who)}</div><div class="bubble">${escapeHtml(text)}</div>`;
  transcript.appendChild(wrap);
  transcript.scrollTop = transcript.scrollHeight;
  return wrap.querySelector(".bubble");
}

function renderStoredTranscript(messages) {
  if (!messages || !messages.length) return;
  for (const m of messages) {
    if (m.role === "user") {
      addMsg("user", "you", m.text || "");
    } else if (m.role === "agent" && m.text) {
      const bubble = addMsg("agent", "agent", "");
      if (bubble) renderAgentText(bubble, m.text);
    }
  }
}

function setAuth(state, label) {
  // state: 'ok' | 'bad' | '' (transient) — drives the small sub-line
  // under the user name in the sidebar footer.
  authPill.textContent = label;
  authPill.style.color = state === "ok" ? "#1a7f37"
                       : state === "bad" ? "#cf222e"
                       : "";
  signedIn = (state === "ok");
  const loginBtn = document.getElementById("loginBtn");
  if (loginBtn) loginBtn.style.display = signedIn ? "none" : "";
}

function setUser(name, upn, oid) {
  currentUserUpn = (upn && typeof upn === "string") ? upn.toLowerCase() : null;
  currentUserOid = (oid && typeof oid === "string") ? oid : null;
  const avatar = document.getElementById("userAvatar");
  if (name) {
    userPill.textContent = name;
    if (avatar) {
      avatar.textContent = initials(name);
      avatar.classList.remove("signed-out");
    }
    // Re-render the dashboard now that the user identity is known — tiles that
    // belong to the signed-in user need to show "You", and the first render
    // (from ready()) fires before signin_silent() has populated currentUserUpn.
    if (_lastDashboard) renderDashboard(_lastDashboard);
  } else {
    currentUserUpn = null;
    currentUserOid = null;
    userPill.textContent = "Not signed in";
    if (avatar) {
      avatar.textContent = "?";
      avatar.classList.add("signed-out");
    }
  }
}

function clearActivity() {
  activityList.innerHTML = `<div class="activity-empty">No tool calls yet.</div>`;
  logsList.innerHTML = `<div class="activity-empty">No log entries yet.</div>`;
  activeTool = null;
  shownPhaseCards.clear();
}

// Phase-card definitions — milestone tool calls that get rendered inline in
// the chat stream (Hub-Cowork-style yellow/blue cards). `once: true` means
// only render the first occurrence per turn (e.g. "Kicking off sections"
// shouldn't repeat for each section).
// Phase cards are inserted at the moment a milestone tool fires, but the
// user reads them *after* the turn — so the labels are past-tense and the
// icon is a checkmark, to clearly signal "this happened" rather than "this is
// happening now".
const PHASE_CARDS = {
  start_charter:     { icon: "✓", label: "Charter started",   sub: "wrote project_charter.md and seeded the project log", tone: "" },
  add_charter_task:  { icon: "✓", label: "Sections added",    sub: "one task per section", tone: "info", once: true },
  record_kickoff:    { icon: "✓", label: "Owners kicked off", sub: "Teams DM → email fallback per the communication matrix", tone: "info", once: true },
  dashboard_payload: { icon: "✓", label: "Dashboard prepared", sub: "",                     tone: "muted", once: true },
};

function renderPhaseCard(toolName) {
  const spec = PHASE_CARDS[toolName];
  if (!spec) return;
  if (spec.once && shownPhaseCards.has(toolName)) return;
  shownPhaseCards.add(toolName);
  const card = document.createElement("div");
  card.className = `phase-card ${spec.tone || ""}`;
  card.innerHTML = `<div class="icon">${escapeHtml(spec.icon || "•")}</div>`
    + `<div class="text"><b>${escapeHtml(spec.label)}</b>${spec.sub ? `<div class="sub">${escapeHtml(spec.sub)}</div>` : ""}</div>`;
  // Insert the card BEFORE the in-flight agent bubble so the order reads
  // user → cards → agent. If there's no bubble yet, just append.
  const bubbleWrap = currentAgentMsg ? currentAgentMsg.parentElement : null;
  if (bubbleWrap && bubbleWrap.parentElement === transcript) {
    transcript.insertBefore(card, bubbleWrap);
  } else {
    transcript.appendChild(card);
  }
  transcript.scrollTop = transcript.scrollHeight;
}

// Strip ```json kind=dashboard``` fenced blocks from agent text.
// The dashboard payload is rendered in the top widget; showing the raw JSON
// in the chat bubble is noise. Runs on every streaming delta as well as on
// turn.complete, so we also handle the *partial* case where the opening fence
// has streamed but the closing ``` hasn't arrived yet — strip from the first
// ```json onward if no closing fence is seen.
function stripDashboardJson(text) {
  if (!text) return text;
  let out = text.replace(/```(?:json)?\s*\{[\s\S]*?"kind"\s*:\s*"dashboard"[\s\S]*?\}\s*```/g, "");
  // Handle an in-progress fence whose closing ``` hasn't streamed yet.
  const open = out.lastIndexOf("```json");
  if (open !== -1 && out.indexOf("```", open + 7) === -1) {
    out = out.slice(0, open);
  }
  return out.trim();
}

// Minimal, safe Markdown → HTML for streaming agent text. Handles paragraphs,
// headings (### / ## / #), bullets, ordered lists, **bold**, *italic*,
// `inline code`, and ```fenced code```. Escapes HTML first so nothing the
// model emits can inject markup.
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
// Short relative-ish date for sidebar rows, à la Hub Cowork: "today" /
// "yesterday" / "Mon" within the last week / "May 24" / "May 24, 2025".
function formatProjectDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const now = new Date();
  const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.floor((startOfDay(now) - startOfDay(d)) / 86400000);
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days > 1 && days < 7) return d.toLocaleDateString(undefined, { weekday: "short" });
  if (d.getFullYear() === now.getFullYear()) return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
function mdToHtml(src) {
  if (!src) return "";
  // Pull out fenced code blocks first so inline rules don't touch them.
  // ```markdown / ```md fences are unwrapped and rendered as Markdown (the
  // agent often wraps the charter that way to "show" it — user wants the
  // rendered view, not the source).
  const fences = [];
  let text = src.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, body) => {
    const l = (lang || "").toLowerCase();
    if (l === "markdown" || l === "md" || l === "") {
      // Unwrap; render as Markdown inline by leaving the body in the stream.
      return body.replace(/\n$/, "");
    }
    fences.push(`<pre><code>${escapeHtml(body.replace(/\n$/, ""))}</code></pre>`);
    return `\u0000FENCE${fences.length - 1}\u0000`;
  });
  text = escapeHtml(text);
  // Headings.
  text = text.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  text = text.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  text = text.replace(/^# (.+)$/gm, "<h1>$1</h1>");
  // Table builder: rows[] are already HTML-escaped `| … |` strings.
  // Requires a separator row (|---|) as the second row to qualify as a table.
  function buildTable(rows) {
    if (rows.length < 2) return "<p>" + rows.join("<br>") + "</p>";
    const sep = rows[1];
    if (!/^\|[-:| ]+\|$/.test(sep)) return "<p>" + rows.join("<br>") + "</p>";
    const cells = r => r.replace(/^\||\|$/g, "").split("|").map(c => c.trim());
    let h = "<table><thead><tr>" + cells(rows[0]).map(c => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
    for (const row of rows.slice(2)) {
      h += "<tr>" + cells(row).map(c => `<td>${c}</td>`).join("") + "</tr>";
    }
    return h + "</tbody></table>";
  }
  // Lists/tables: collapse consecutive rows into <ul>/<ol>/<table>.
  const lines = text.split("\n");
  const out = [];
  let listType = null;          // 'ul' | 'ol' | null
  let para = [];
  let tableRows = [];           // accumulate consecutive table-row lines
  function flushPara() {
    if (para.length) { out.push("<p>" + para.join("<br>") + "</p>"); para = []; }
  }
  function flushList() {
    if (listType) { out.push(`</${listType}>`); listType = null; }
  }
  function flushTable() {
    if (tableRows.length) { out.push(buildTable(tableRows)); tableRows = []; }
  }
  for (const line of lines) {
    const trimmed = line.trim();
    // Table row: starts and ends with | and is not just `|`
    if (trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.length > 1) {
      flushPara(); flushList();
      tableRows.push(trimmed);
      continue;
    }
    flushTable(); // end any open table before processing other line types
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (bullet) {
      flushPara();
      if (listType !== "ul") { flushList(); out.push("<ul>"); listType = "ul"; }
      out.push("<li>" + bullet[1] + "</li>");
    } else if (ordered) {
      flushPara();
      if (listType !== "ol") { flushList(); out.push("<ol>"); listType = "ol"; }
      out.push("<li>" + ordered[1] + "</li>");
    } else if (trimmed === "") {
      flushList();
      flushPara();
    } else if (/^<h\d>/.test(line)) {
      flushList();
      flushPara();
      out.push(line);
    } else {
      flushList();
      para.push(line);
    }
  }
  flushList();
  flushPara();
  flushTable();
  let html = out.join("\n");
  // Inline: **bold**, *italic*, `code`.
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  // Restore code fences.
  html = html.replace(/\u0000FENCE(\d+)\u0000/g, (_, i) => fences[Number(i)]);
  return html;
}
function renderAgentText(node, raw) {
  if (!node) return;
  node.innerHTML = mdToHtml(stripDashboardJson(raw));
}

function tsNow() {
  const d = new Date();
  return d.toTimeString().slice(0, 8);
}

function addLogLine(kind, name, detail) {
  const empty = logsList.querySelector(".activity-empty");
  if (empty) empty.remove();
  const node = document.createElement("div");
  node.className = `log-line ${kind}`;
  node.innerHTML = `<span class="ts">${escapeHtml(tsNow())}</span>`
    + `<span class="name">${escapeHtml(name)}</span>`
    + (detail ? ` ${escapeHtml(detail)}` : "");
  logsList.appendChild(node);
  logsList.scrollTop = logsList.scrollHeight;
}

// ---- Logs tab: render the agent's audit trail from activity.json ----
// This is the agent's own product audit log (`$HOME/activity.json` written
// by `observability.log_activity`). It survives client restarts; the bridge
// reads it from disk and pushes the tail via `view.update` events.

function fmtAuditTime(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso.slice(11, 19);
    return d.toLocaleTimeString([], { hour12: false }) + " " + d.toLocaleDateString();
  } catch { return iso; }
}

function renderActivityLog(rows) {
  if (!Array.isArray(rows)) return;
  _lastActivity = rows;
  logsList.innerHTML = "";
  if (!rows.length) {
    logsList.innerHTML = `<div class="activity-empty">No audit entries yet — the agent appends to activity.json as work happens.</div>`;
    return;
  }
  for (const r of rows) {
    const node = document.createElement("div");
    const kindClass = (r.kind || "").includes("error") ? "err" : "tool";
    node.className = `log-line ${kindClass}`;
    node.innerHTML =
      `<span class="ts">${escapeHtml(fmtAuditTime(r.at))}</span>` +
      `<span class="name">${escapeHtml(r.kind || "")}</span>` +
      ` <span style="color:var(--muted)">${escapeHtml(r.actor || "")}</span>` +
      (r.summary ? ` — ${escapeHtml(r.summary)}` : "") +
      (r.ref ? ` <span style="color:var(--muted)">(${escapeHtml(r.ref)})</span>` : "");
    logsList.appendChild(node);
  }
  logsList.scrollTop = logsList.scrollHeight;
}

// Map raw tool names → short human labels for business users.
// In-process tools are listed explicitly; Toolbox tools fall back to a
// service-derived label like "Mail · search_messages".
const TOOL_LABELS = {
  load_project_state: "Checking project state",
  start_charter: "Starting project charter",
  add_charter_task: "Adding a section",
  record_kickoff: "Recording kickoff",
  record_submission: "Recording a submission",
  mark_task_polled: "Updating task status",
  record_nudge_sent: "Recording nudge",
  dashboard_payload: "Preparing dashboard",
  state_read_text: "Reading project file",
  state_read_json: "Reading project file",
  state_write_text: "Writing project file",
  state_write_json: "Writing project file",
  state_list_files: "Listing project files",
  state_file_exists: "Checking project file",
  log_workflow_step: "Logging activity",
  project_read_log: "Reading project state",
  project_patch_log: "Updating project state",
  project_write_log: "Saving project state",
  invoke_skill: "Running workflow step",
};

// Friendly progress labels shown while each SOW sub-skill is running.
// Keyed by skill name; updated when tool.args fires with skill_name.
const SKILL_LABELS = {
  "sow-rfp-search":      "Searching for RFP",
  "sow-charter-draft":   "Drafting SOW charter",
  "sow-kickoff-extract": "Finding kickoff meeting notes",
  "sow-task-allocate":   "Sending kickoff messages",
  "sow-reply-poll":      "Checking for replies",
};

const SERVICE_LABELS = {
  WorkIQMail2: "Mail",
  WorkIQTeams: "Teams",
  WorkIQCalendar2: "Calendar",
  WorkIQSharePoint2: "SharePoint",
  WorkIQOneDrive: "OneDrive",
  WorkIQWord: "Word",
  WorkIQUser: "People",
  WorkIQCopilot: "Copilot",
};

function humanizeTool(rawName) {
  if (!rawName) return "Working…";
  if (TOOL_LABELS[rawName]) return TOOL_LABELS[rawName];
  const m = rawName.match(/^([A-Za-z0-9]+)___(.+)$/);
  if (m) {
    const svc = SERVICE_LABELS[m[1]] || m[1];
    const action = m[2].replace(/_/g, " ");
    return `${svc} · ${action}`;
  }
  return rawName;
}

function addActivity(name) {
  const empty = activityList.querySelector(".activity-empty");
  if (empty) empty.remove();
  const node = document.createElement("div");
  node.className = "act running";
  node.innerHTML = `<span class="dur">0.0s</span><div class="name">${escapeHtml(humanizeTool(name))}</div>`;
  activityList.appendChild(node);
  activityList.scrollTop = activityList.scrollHeight;
  const startedAt = performance.now();
  const dur = node.querySelector(".dur");
  const ticker = setInterval(() => {
    dur.textContent = `${((performance.now() - startedAt) / 1000).toFixed(1)}s`;
  }, 100);
  return { node, startedAt, ticker, name };
}

// Pick a couple of human-meaningful key/value pairs from a tool-args JSON
// blob so the activity row tells the user *what* the agent is doing, not just
// which method. Falls back to the first 80 chars of any plain string.
const ARG_PRIORITY = [
  "subject", "title", "to", "recipient", "recipient_upn", "recipients",
  "owner_upn", "owner", "owner_display_name",
  "task_id", "section",
  "path", "file", "name",
  "channel", "kind", "status",
  "query", "q", "message",
];

function shorten(v, max = 90) {
  if (v == null) return "";
  let s = typeof v === "string" ? v : JSON.stringify(v);
  s = s.replace(/\s+/g, " ").trim();
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

function formatToolContext(argsString) {
  if (!argsString) return "";
  let obj;
  try { obj = JSON.parse(argsString); } catch { return shorten(argsString); }
  if (obj == null || typeof obj !== "object") return shorten(argsString);
  const parts = [];
  for (const k of ARG_PRIORITY) {
    if (k in obj && obj[k] !== "" && obj[k] !== null && obj[k] !== undefined) {
      const v = Array.isArray(obj[k]) ? obj[k].join(", ") : obj[k];
      parts.push(`<b>${escapeHtml(k)}:</b> ${escapeHtml(shorten(v))}`);
      if (parts.length >= 2) break;
    }
  }
  if (!parts.length) {
    // Fall back to whatever the first scalar key is.
    for (const [k, v] of Object.entries(obj)) {
      if (v == null || typeof v === "object") continue;
      parts.push(`<b>${escapeHtml(k)}:</b> ${escapeHtml(shorten(v))}`);
      break;
    }
  }
  return parts.join(" &nbsp;·&nbsp; ");
}

function decorateActiveTool(argsString) {
  if (!activeTool || !activeTool.node) return;
  const ctx = formatToolContext(argsString);
  if (!ctx) return;
  let argsEl = activeTool.node.querySelector(".args");
  if (!argsEl) {
    argsEl = document.createElement("div");
    argsEl.className = "args";
    activeTool.node.appendChild(argsEl);
  }
  argsEl.innerHTML = ctx;
}

function finishActiveTool(state) {
  if (!activeTool) return;
  const { node, startedAt, ticker } = activeTool;
  if (ticker) clearInterval(ticker);
  node.classList.remove("running");
  node.classList.add(state || "done");
  const dur = node.querySelector(".dur");
  if (dur) dur.textContent = `${((performance.now() - startedAt) / 1000).toFixed(1)}s`;
  activeTool = null;
}


function setSession(sid) {
  sessionMeta.dataset.sid = sid || "";
  sessionMeta.title = sid ? `Session ID (click to copy):\n${sid}` : "Foundry session id";
  sessionMeta.textContent = sid
    ? `session: ${sid.slice(0, 16)}…`
    : "session: (none — platform will assign)";
}

sessionMeta.addEventListener("click", () => {
  const sid = sessionMeta.dataset.sid;
  if (!sid) return;
  navigator.clipboard.writeText(sid).then(() => {
    const prev = sessionMeta.textContent;
    sessionMeta.textContent = "session ID copied ✓";
    setTimeout(() => { sessionMeta.textContent = prev; }, 1800);
  }).catch(() => {
    // Fallback: select the full ID from title via prompt
    prompt("Copy session ID:", sid);
  });
});

function setEndpoint(mode, url) {
  endpointMeta.textContent = `endpoint: ${mode} → ${url || "(not configured)"}`;
  modeSelect.value = mode;
}

// Activity entries are loaded by the bridge from `<HOME>/activity.json` and
// pushed via `view.update` events. Cached so renderDashboard can paint the
// stream into its right column even when the agent only sends a fresh
// dashboard payload (e.g. on turn.complete).
let _lastActivity = [];
let _lastDashboard = null;

function initials(name) {
  if (!name) return "?";
  const parts = String(name).trim().split(/\s+/).slice(0, 2);
  return parts.map(p => p[0] || "").join("").toUpperCase() || "?";
}

const AVATAR_PALETTE = [
  "#5b6af0","#e05e94","#0aa4a4","#e07f35","#7c4dce","#2e9e5e","#c04040","#3a7ebf"
];
function avatarColor(name) {
  if (!name) return AVATAR_PALETTE[0];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffff;
  return AVATAR_PALETTE[h % AVATAR_PALETTE.length];
}

function fmtStreamTime(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const today = new Date();
    const same = d.toDateString() === today.toDateString();
    const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
    return same ? `Today, ${t}` : `${d.toLocaleDateString()} ${t}`;
  } catch { return iso; }
}

const STATUS_LABEL = {
  assigned: "Assigned",
  inprogress: "In progress",
  in_progress: "In progress",
  atrisk: "At risk",
  overdue: "At risk",
  submitted: "Submitted",
  submitted_with_gaps: "Submitted (gaps)",
  reconcile: "Reconcile",
  kicked_off: "Kicked off",
  closed: "Closed",
};
// Header-level SOW workflow status labels (distinct from per-section tile labels).
// "In progress" at the project level means "collecting owner responses" — not that
// the agent is currently running. Use clearer wording to avoid that confusion.
const WORKFLOW_STATUS_LABEL = {
  drafted:             "Drafting",
  kicked_off:          "Awaiting replies",
  in_progress:         "Collecting",
  submitted_with_gaps: "Gaps remaining",
  submitted:           "All submitted",
  closed:              "Closed",
};

function renderDashboard(d) {
  if (!d) { return; }
  _lastDashboard = d;
  const sections = Array.isArray(d.sections) ? d.sections : [];
  const exceptions = Array.isArray(d.exceptions) ? d.exceptions : [];
  const prog = d.progress || { submitted: 0, total: sections.length || 0 };
  const pct = prog.total ? Math.round(100 * (prog.submitted || 0) / prog.total) : 0;
  const headerStatus = d.status || "kicked_off";

  // Hide "Kick off a new SOW" once an SOW is underway for this project.
  const sowActive = sections.length > 0;
  const kickoffBtn = document.getElementById("kickoffSowBtn");
  const kickoffSep = document.getElementById("kickoffSowSep");
  if (kickoffBtn) kickoffBtn.style.display = sowActive ? "none" : "";
  if (kickoffSep) kickoffSep.style.display = sowActive ? "none" : "";
  // Normalize to CSS class name (in_progress → inprogress) and pick the clearer
  // workflow-level label so the pill reads "Collecting" rather than "In progress".
  const headerCssClass = headerStatus.replace(/_/g, "");
  const headerLabel = WORKFLOW_STATUS_LABEL[headerStatus] || STATUS_LABEL[headerStatus] || headerStatus;

  const tiles = sections.map(s => {
    const st = s.status || "assigned";
    const label = STATUS_LABEL[st] || st;
    // Prefer OID (immutable Entra GUID, alias-proof). Fall back to UPN only
    // for project logs that predate the owner_oid field.
    const isYou = !!(
      (currentUserOid && s.owner_oid && s.owner_oid === currentUserOid) ||
      (!s.owner_oid && currentUserUpn && s.owner_upn &&
       s.owner_upn.toLowerCase() === currentUserUpn)
    );
    const displayOwner = isYou ? "You" : escapeHtml(s.owner || "—");
    const avatarClass = isYou ? "avatar you" : "avatar";
    const avatarStyle = isYou ? "" : ` style="background:${avatarColor(s.owner || s.task_id)}"`;
    return `
      <div class="tile tile-${escapeHtml(st)}">
        <div class="tile-head">
          <div class="${avatarClass}"${avatarStyle}>${escapeHtml(initials(s.owner || s.task_id))}</div>
          <div class="tile-id">
            <div class="name">${displayOwner}</div>
            <div class="role">${escapeHtml(s.task_id || "")}</div>
          </div>
        </div>
        <div class="section">${escapeHtml(s.title || "—")}</div>
        <div><span class="pill ${escapeHtml(st === "overdue" ? "atrisk" : st)}">${escapeHtml(label)}</span></div>
        ${s.last_signal ? `<div class="signal">${escapeHtml(s.last_signal)}</div>` : ""}
      </div>`;
  }).join("");

  const exceptionsHtml = exceptions.length
    ? exceptions.map(e => `<div class="exception ${escapeHtml(e.kind || "")}"><b>${escapeHtml(e.title || "")}</b><br/>${escapeHtml(e.body || "")}</div>`).join("")
    : `<div class="dash-empty" style="padding:0;">Nothing needs you right now.</div>`;

  // Stream: newest first; show last 20 in the dashboard panel.
  const streamRows = (_lastActivity || []).slice(-20).reverse();
  const streamHtml = streamRows.length
    ? streamRows.map(r => `
        <div class="stream-item">
          <div class="body">${escapeHtml(r.summary || r.kind || "")}</div>
          <div class="foot">${escapeHtml(fmtStreamTime(r.at))} · ${escapeHtml(r.actor || "")}</div>
        </div>`).join("")
    : `<div class="dash-empty" style="padding:0;">No activity yet.</div>`;

  const deliverableHtml = d.deliverable_url && /^https?:\/\//i.test(d.deliverable_url)
    ? `<a class="deliverable-link" href="${escapeHtml(d.deliverable_url)}" target="_blank" rel="noopener">Open the consolidated deliverable →</a>`
    : "";

  const proposalTitle = prog.submitted > 0
    ? `Consolidating ${prog.submitted} of ${prog.total} sections`
    : "Awaiting first submission";
  const proposalMeta = prog.submitted > 0
    ? "Sections are being assembled as drafts arrive"
    : "Sections will be assembled as drafts arrive";
  const dueLine = d.due ? `Due ${escapeHtml(d.due)}` : "";

  dashboard.innerHTML = `
    <div class="dash-head">
      <div>
        <div class="dash-title">${escapeHtml(d.project || d.customer || "Project")}${d.customer && d.project ? ` — ${escapeHtml(d.customer)}` : ""}</div>
        <div class="dash-sub">${escapeHtml(headerLabel)}${dueLine ? ` · ${dueLine}` : ""}</div>
      </div>
      <span class="pill ${escapeHtml(headerCssClass)}" title="SOW workflow status">${escapeHtml(headerLabel)}</span>
    </div>
    <div class="dash-row">
      <div>
        <div class="section-label">Section owners</div>
        <div class="tiles">${tiles || '<div class="dash-empty" style="padding:0;">No sections yet.</div>'}</div>

        <div class="proposal-card">
          <div class="proposal-head">
            <div>
              <div class="proposal-title">${escapeHtml(proposalTitle)}</div>
              <div class="proposal-meta">${escapeHtml(proposalMeta)}</div>
            </div>
            <div class="proposal-pct">${pct}%</div>
          </div>
          <div class="proposal-steps">${
            Array.from({length: prog.total || 4}, (_, i) => {
              const cls = i < (prog.submitted || 0) ? "done" : (i === (prog.submitted || 0) && pct > 0 ? "partial" : "");
              return `<div class="proposal-step ${cls}"></div>`;
            }).join("")
          }</div>
          ${deliverableHtml ? `<div style="margin-top:8px;">${deliverableHtml}</div>` : ""}
        </div>
      </div>
      <div>
        <div class="section-label">Exceptions</div>
        <div class="exceptions" style="margin-bottom:18px;">${exceptionsHtml}</div>

        <div class="section-label">Activity stream <span class="hint">· most recent first</span></div>
        <div class="activity-stream">${streamHtml}</div>
      </div>
    </div>`;
}

function setBusy(b) {
  busy = b;
  sendBtn.disabled = b;
  document.querySelectorAll(".qa").forEach(el => { el.disabled = b; });
  promptEl.disabled = b;
}

async function ensureSignedIn() {
  if (signedIn) return true;
  const r = await window.pywebview.api.login();
  if (r && r.ok) {
    setAuth("ok", "signed in");
    return true;
  }
  setAuth("bad", "sign-in failed");
  addMsg("error", "system", (r && r.error) || "sign-in failed");
  return false;
}

async function sendPrompt(text) {
  text = (text || "").trim();
  if (!text || busy) return;
  if (!await ensureSignedIn()) return;

  addMsg("user", "you", text);
  currentAgentBuffer = "";
  currentAgentMsg = addMsg("agent", "agent", "");
  currentAgentMsg.classList.add("thinking");
  currentAgentMsg.setAttribute("data-thinking-label", "Thinking…");
  setBusy(true);
  const r = await window.pywebview.api.send(text);
  if (!r || !r.ok) {
    currentAgentMsg.textContent = `error: ${(r && r.error) || "unknown"}`;
    currentAgentMsg.parentElement.classList.add("error");
    setBusy(false);
  }
}

// ---- agent-event sink (called from Python via evaluate_js) ----

window.onAgentEvent = function (msg) {
  if (!msg || !msg.event) return;
  const p = msg.payload || {};
  switch (msg.event) {
    case "turn.start":
      _currentTurnIsAuto = p.auto === true;
      if (_currentTurnIsAuto) {
        setSchedStatus("checking", `Checking ${p.project_label || "project"}…`);
      }
      break;
    case "session.update":
      setSession(p.session_id);
      break;
    case "tool.call":
      finishActiveTool("done");
      activeTool = addActivity(p.name);
      renderPhaseCard(p.name);
      if (currentAgentMsg && currentAgentMsg.classList.contains("thinking")) {
        currentAgentMsg.setAttribute("data-thinking-label", humanizeTool(p.name) + "…");
      }
      break;
    case "tool.args":
      decorateActiveTool(p.args);
      // When invoke_skill fires, replace the generic label with the friendly skill name.
      if (activeTool && activeTool.name === "invoke_skill" && p.args) {
        try {
          const parsed = typeof p.args === "string" ? JSON.parse(p.args) : p.args;
          const friendlyLabel = parsed && parsed.skill_name && SKILL_LABELS[parsed.skill_name];
          if (friendlyLabel) {
            const nameEl = activeTool.node && activeTool.node.querySelector(".name");
            if (nameEl) nameEl.textContent = friendlyLabel;
            const argsEl = activeTool.node && activeTool.node.querySelector(".args");
            if (argsEl) argsEl.remove();
            if (currentAgentMsg && currentAgentMsg.classList.contains("thinking")) {
              currentAgentMsg.setAttribute("data-thinking-label", friendlyLabel + "…");
            }
          }
        } catch (_) {}
      }
      // When log_workflow_step fires, show the summary text instead of "Logging activity".
      if (activeTool && activeTool.name === "log_workflow_step" && p.args) {
        try {
          const parsed = typeof p.args === "string" ? JSON.parse(p.args) : p.args;
          const summary = parsed && parsed.summary;
          if (summary) {
            const nameEl = activeTool.node && activeTool.node.querySelector(".name");
            if (nameEl) nameEl.textContent = summary;
            const argsEl = activeTool.node && activeTool.node.querySelector(".args");
            if (argsEl) argsEl.remove();
          }
        } catch (_) {}
      }
      break;
    case "text.delta":
      finishActiveTool("done");
      if (currentAgentMsg && currentAgentMsg.classList.contains("thinking")) {
        currentAgentMsg.classList.remove("thinking");
        currentAgentMsg.removeAttribute("data-thinking-label");
      }
      currentAgentBuffer += p.delta || "";
      if (currentAgentMsg) renderAgentText(currentAgentMsg, currentAgentBuffer);
      transcript.scrollTop = transcript.scrollHeight;
      break;
    case "turn.complete":
      finishActiveTool("done");
      if (_currentTurnIsAuto) {
        // Auto turn: update dashboard silently, no chat bubble, don't touch busy state.
        if (p.dashboard) renderDashboard(p.dashboard);
        if (p.session_id) setSession(p.session_id);
        shownPhaseCards.clear();
        _currentTurnIsAuto = false;
      } else {
        if (currentAgentMsg) {
          currentAgentMsg.classList.remove("thinking");
          currentAgentMsg.removeAttribute("data-thinking-label");
        }
        {
          const finalRaw = p.text || currentAgentBuffer;
          if (finalRaw && currentAgentMsg) renderAgentText(currentAgentMsg, finalRaw);
        }
        if (p.dashboard) renderDashboard(p.dashboard);
        if (p.session_id) setSession(p.session_id);
        shownPhaseCards.clear();
        setBusy(false);
        currentAgentMsg = null;
      }
      break;
    case "turn.error":
      finishActiveTool("error");
      if (_currentTurnIsAuto) {
        setSchedStatus("", "Auto-check failed — will retry next cycle");
        _currentTurnIsAuto = false;
      } else {
        if (currentAgentMsg) {
          currentAgentMsg.classList.remove("thinking");
          currentAgentMsg.removeAttribute("data-thinking-label");
          currentAgentMsg.textContent = p.error || "Something went wrong — please try again.";
          currentAgentMsg.parentElement.classList.add("error");
        } else {
          addMsg("error", "system", p.error || "Something went wrong — please try again.");
        }
        shownPhaseCards.clear();
        setBusy(false);
      }
      break;
    case "consent.required":
      addMsg("system", "consent", `Opening consent URL in browser: ${p.url}\nAfter you grant consent, the turn will retry automatically.`);
      break;
    case "projects.update":
      renderProjects(p);
      break;
    case "view.update":
      // Only paint dashboard/activity when this view belongs to the active project.
      // Background auto-checks for other projects still update the cache but must
      // not overwrite what the user is currently looking at.
      if (p.project_id && p.project_id !== activeProjectId) break;
      // Activity first so the cache is fresh before the dashboard paints
      // its right-column stream.
      if (p.activity) renderActivityLog(p.activity);
      if (p.dashboard) renderDashboard(p.dashboard);
      break;
    case "skill.routed":
      // First-turn skill routing is now server-side; this event remains for
      // observability only (no client action needed).
      break;
    case "session.forked":
      addMsg("system", "system",
        "⚠ Foundry started a new session (the previous one was recycled). " +
        "Your sandbox VM state has been reset. To restore the project, " +
        "kick off the SOW again — the agent will rebuild from your email and meeting notes.");
      break;
    case "scheduler.tick":
      setSchedStatus("checking", `Checking ${p.project_label || "project"}…`);
      break;
    case "scheduler.done": {
      const nChanges = (p.changes || []).length;
      const nextMins = p.next_in_mins || _pollIntervalMins;
      const isActive = (!p.project_id || p.project_id === activeProjectId);
      // Re-enable "Run now" button in case it triggered this cycle.
      if (runNowBtn) runNowBtn.disabled = false;
      // Show a brief summary, then the countdown timer takes over after one tick.
      if (nChanges > 0) {
        setSchedStatus("updated", `${nChanges} update${nChanges !== 1 ? "s" : ""} found`);
      } else {
        setSchedStatus("", "Checked — no new replies");
      }
      _armSchedCountdown(nextMins);
      // Always show an in-app toast so the user knows the check ran.
      // hasUpdates drives colour and dismiss duration; clickPid only set
      // for non-active projects (clicking switches to that project).
      {
        const label = p.project_label || p.project_id || "Project";
        const msg = nChanges > 0
          ? `${nChanges} section update${nChanges !== 1 ? "s" : ""} received`
          : "Auto-check complete — no new replies";
        showToast(label, msg, {
          clickPid: isActive ? null : p.project_id,
          hasUpdates: nChanges > 0,
        });
      }
      break;
    }
  }
};

// ---- wiring ----

sendBtn.addEventListener("click", () => sendPrompt(promptEl.value).then(() => { promptEl.value = ""; }));

// "Run now" triggers the same code path as the background poller so it produces
// scheduler.tick / scheduler.done events and thus toast + OS notifications.
const runNowBtn = $("runNowBtn");
if (runNowBtn) {
  runNowBtn.addEventListener("click", async () => {
    runNowBtn.disabled = true;
    const r = await window.pywebview?.api?.run_now?.();
    if (!r || !r.ok) {
      setSchedStatus("", r?.error || "Check failed to start");
      setTimeout(() => { _tickSchedCountdown(); runNowBtn.disabled = false; }, 3000);
    }
    // scheduler.done re-enables the button when the check finishes.
  });
}
promptEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const v = promptEl.value;
    promptEl.value = "";
    sendPrompt(v);
  }
});
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t === tab));
    const paneId = tab.getAttribute("data-pane");
    document.querySelectorAll(".pane").forEach(p => p.classList.toggle("hidden", p.id !== paneId));
  });
});
const toggleSidebarBtn = document.getElementById("toggleSidebarBtn");
const middleEl = document.querySelector(".middle");
toggleSidebarBtn.addEventListener("click", () => {
  middleEl.classList.toggle("sidebar-collapsed");
});

loginBtn.addEventListener("click", async () => {
  setAuth("", "signing in…");
  const r = await window.pywebview.api.login();
  if (r && r.ok) {
    setAuth("ok", "signed in");
    setUser(r.user_name, r.user_upn, r.user_oid);
  } else {
    setAuth("bad", "sign-in failed");
    setUser(null);
    addMsg("error", "system", (r && r.error) || "sign-in failed");
  }
});
resetBtn.addEventListener("click", async () => {
  await window.pywebview.api.reset_session();
  setSession(null);
  clearActivity();
  dashboard.innerHTML = `<div class="dash-empty">Session reset. The next message will create a fresh Foundry sandbox under the current project.</div>`;
  addMsg("system", "system", "Session reset. Same project, fresh Foundry sandbox.");
});

// ---- projects sidebar ----

const projListEl = document.getElementById("projList");
const newProjectBtn = document.getElementById("newProjectBtn");
let activeProjectId = null;

function renderProjects(payload) {
  if (!payload || !payload.list) return;
  activeProjectId = payload.active;
  if (!payload.list.length) {
    projListEl.innerHTML = `<div class="activity-empty" style="padding: 8px 12px;">No projects yet.</div>`;
    return;
  }
  projListEl.innerHTML = "";
  for (const p of payload.list) {
    const row = document.createElement("div");
    row.className = "proj-item" + (p.id === payload.active ? " active" : "");
    row.dataset.pid = p.id;
    const top = document.createElement("div");
    top.className = "row";
    const lbl = document.createElement("div");
    lbl.className = "label";
    lbl.textContent = p.label || "New project";
    const date = document.createElement("span");
    date.className = "date";
    date.textContent = formatProjectDate(p.created_at || p.last_used_at || "");
    const del = document.createElement("button");
    del.className = "del";
    del.textContent = "✕";
    del.title = "Delete this project";
    del.addEventListener("click", async (e) => {
      e.stopPropagation();
      const warning = modeSelect.value === "local"
        ? `Delete project "${p.label}"?\n\nThis permanently removes the project folder and all its files from disk. This cannot be undone.`
        : `Delete project "${p.label}"?\n\nThis attempts to delete the Foundry session microVM and removes the project from your sidebar. This cannot be undone.`;
      if (!confirm(warning)) return;
      const wasActive = (p.id === activeProjectId);
      const r = await window.pywebview.api.delete_project(p.id);
      if (r && r.ok) {
        renderProjects(r);
        if (wasActive) {
          // we deleted the active one; the bridge picked a new active — load it
          await switchToActive();
        }
      }
    });
    top.appendChild(lbl);
    top.appendChild(date);
    top.appendChild(del);
    const sub = document.createElement("div");
    sub.className = "sub";
    const skill = (p.skill || "").trim();
    const detail = p.customer_name && p.customer_name !== p.label ? p.customer_name : (p.is_new ? "not started" : p.id);
    const skillHtml = skill ? `<span class="skill-tag">${escapeHtml(skill)}</span>` : "";
    sub.innerHTML = `${skillHtml}${escapeHtml(detail)}`;
    row.appendChild(top);
    row.appendChild(sub);
    row.addEventListener("click", async () => {
      if (p.id === activeProjectId) return;
      const r = await window.pywebview.api.switch_project(p.id);
      if (r && r.ok) {
        renderProjects(r);
        loadActiveProjectView(r);
      }
    });
    projListEl.appendChild(row);
  }
}

function clearTranscript() {
  const t = document.getElementById("transcript");
  if (t) t.innerHTML = "";
  currentAgentMsg = null;
  shownPhaseCards.clear();
}

function loadActiveProjectView(payload) {
  // Switching projects clears the conversation view. The bridge already
  // restored this project's session_id / previous_response_id; the next
  // message resumes (or starts) that project's Foundry session.
  clearTranscript();
  clearActivity();
  const view = payload && payload.view;
  if (view && view.transcript && view.transcript.length) renderStoredTranscript(view.transcript);
  if (view && view.activity) renderActivityLog(view.activity);
  if (view && view.dashboard) {
    renderDashboard(view.dashboard);
  } else {
    // No dashboard yet — show the kickoff button so a fresh project can start.
    const kickoffBtn = document.getElementById("kickoffSowBtn");
    const kickoffSep = document.getElementById("kickoffSowSep");
    if (kickoffBtn) kickoffBtn.style.display = "";
    if (kickoffSep) kickoffSep.style.display = "";
    const _ap = payload && payload.list && (payload.list.find(p => p.id === payload.active) || {});
    const _label = (_ap && _ap.label) || "project";
    const _skill = (_ap && _ap.skill) || "";
    const _skillHints = {
      "sow-response": `Ask "show me where we are" to get the latest digest, or use a quick-action.`,
    };
    const _hint = _skillHints[_skill] || `Ask me anything, or say "I have an RFP" to start an SOW response.`;
    dashboard.innerHTML = `<div class="dash-empty">Switched to <b>${escapeHtml(_label)}</b>. ${_hint}</div>`;
  }
  setSession((payload && payload.session_id) || null);
}

async function switchToActive() {
  const r = await window.pywebview.api.list_projects();
  if (r && r.ok) {
    renderProjects(r);
    loadActiveProjectView(r);
  }
}

newProjectBtn.addEventListener("click", async () => {
  const r = await window.pywebview.api.new_project();
  if (r && r.ok) {
    renderProjects(r);
    loadActiveProjectView(r);
    addMsg("system", "system", "New project ready. Ask me anything, or say \"I have an RFP\" to kick off an SOW response.");
  }
});
modeSelect.addEventListener("change", async () => {
  const wanted = modeSelect.value;
  const r = await window.pywebview.api.set_mode(wanted);
  if (!r || !r.ok) {
    addMsg("error", "system", (r && r.error) || `failed to switch to ${wanted}`);
    // Revert dropdown to whatever the bridge actually has.
    const ctx = await window.pywebview.api.ready();
    setEndpoint(ctx.mode, ctx.agent_url);
    return;
  }
  // Endpoint switch: drop UI state tied to the previous endpoint's session.
  // session_id / previous_response_id are minted per-endpoint and don't
  // transfer; the bridge already cleared them, so wipe the transcript +
  // activity here too or the previous conversation bleeds into the new view.
  setSession(null);
  clearTranscript();
  clearActivity();
  setEndpoint(r.mode, r.agent_url);
  if (r.projects) renderProjects(r.projects);
  if (r.view) {
    if (r.view.activity) renderActivityLog(r.view.activity);
    if (r.view.dashboard) renderDashboard(r.view.dashboard);
    else dashboard.innerHTML = `<div class="dash-empty">Switched to ${r.mode} endpoint. Next message creates a fresh sandbox there.</div>`;
  }
  addMsg("system", "system", `Switched endpoint → ${r.mode} (${r.agent_url}). Project list now scoped to ${r.mode}.`);
});
document.querySelectorAll(".qa").forEach(el => {
  el.addEventListener("click", () => sendPrompt(el.dataset.prompt));
});

// ---- boot ----

window.addEventListener("pywebviewready", async () => {
  const ctx = await window.pywebview.api.ready();
  if (ctx && ctx.session_id) setSession(ctx.session_id);
  if (ctx && ctx.user_name) setUser(ctx.user_name, ctx.user_upn, ctx.user_oid);
  if (ctx && ctx.projects) renderProjects(ctx.projects);
  if (ctx && ctx.view) {
    if (ctx.view.activity) renderActivityLog(ctx.view.activity);
    if (ctx.view.dashboard) renderDashboard(ctx.view.dashboard);
  }
  if (ctx && ctx.poll_interval_mins) {
    _armSchedCountdown(ctx.poll_interval_mins);
  }
  if (ctx) {
    setEndpoint(ctx.mode, ctx.agent_url);
    // Disable dropdown options whose endpoint isn't configured.
    const eps = ctx.endpoints || {};
    for (const opt of modeSelect.options) {
      if (!eps[opt.value]) {
        opt.textContent = `${opt.textContent} — not configured`;
        opt.disabled = true;
      }
    }
    // If we have a saved AuthenticationRecord from a previous launch, try a
    // silent token refresh so the UI shows "signed in" without a popup.
    if (ctx.has_record && !ctx.user_name) {
      setAuth("", "signing in…");
      try {
        const sr = await window.pywebview.api.signin_silent();
        if (sr && sr.ok) {
          setAuth("ok", "signed in");
          setUser(sr.user_name, sr.user_upn, sr.user_oid);
        } else {
          setAuth("", "signed out");
        }
      } catch (e) {
        setAuth("", "signed out");
      }
    }
  }
});
