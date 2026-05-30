"""Configuration, constants, env loading, paths, logging, and SDK guards.

This is the foundation module every other client module imports. Importing it
has side effects that MUST run exactly once and before anything reads
env-derived constants:

  1. Mutes pywebview's noisy WinForms backend loggers and installs a filter
     that drops the AccessibilityObject recursion spam.
  2. Loads ``desktop-client/.env`` then ``agent/.env`` (shell vars always win).
  3. Configures the ``charter_desktop`` logger (optional file handler).

Path anchoring note: this file lives in ``desktop-client/charter_client/``, so
paths are computed from explicit ``CLIENT_DIR`` / ``REPO_ROOT`` anchors rather
than ``__file__.with_name(...)`` — keeping them identical to the original
single-file ``app.py`` which lived one level up.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
from typing import Any

import httpx

# Silence pywebview's WinForms backend logger. On WebView2 shutdown it tries to
# repr `window.native` for diagnostics and trips a recursive
# AccessibilityObject.Bounds.Empty loop, dumping multi-KB stderr stacks.
# Harmless but spammy; mute it for the desktop tool.
for _name in ("pywebview", "webview", "pywebview.winforms"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)


class _SuppressPywebviewRecursion(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "AccessibilityObject" not in msg and "maximum recursion depth" not in msg


logging.getLogger().addFilter(_SuppressPywebviewRecursion())

# azure-identity-broker is optional but strongly preferred on Windows.
try:
    from azure.identity.broker import InteractiveBrowserBrokerCredential  # type: ignore
    _BROKER_AVAILABLE = True
except Exception:  # noqa: BLE001
    InteractiveBrowserBrokerCredential = None  # type: ignore[assignment]
    _BROKER_AVAILABLE = False

# Foundry hosted-agent client SDK (preview). This is the idiomatic transport for
# hosted mode: it mints a server-side session via beta.agents.create_session()
# and drives the Responses API through get_openai_client().responses.create()
# with typed streaming events. If the package isn't installed we silently fall
# back to the retained raw-httpx transport (see _post_one_legacy), so the app
# still runs without it.
try:
    from azure.ai.projects import AIProjectClient  # type: ignore
    from azure.ai.projects.models import VersionRefIndicator  # type: ignore
    from azure.core.credentials import AccessToken  # type: ignore
    _SDK_AVAILABLE = True
except Exception:  # noqa: BLE001
    AIProjectClient = None  # type: ignore[assignment]
    VersionRefIndicator = None  # type: ignore[assignment]
    AccessToken = None  # type: ignore[assignment]
    _SDK_AVAILABLE = False

DEFAULT_LOCAL = "http://localhost:8088/responses"
DEFAULT_HOSTED_AGENT_NAME = "charter-agent"
TENANT_ID = os.environ.get("SPIKE_TENANT_ID") or None
SCOPE = "https://ai.azure.com/.default"
HTTP_TIMEOUT = httpx.Timeout(600.0, connect=30.0)

# Directory anchors. This module lives one level deeper than the original
# app.py (charter_client/config.py vs app.py), so compute explicit anchors.
_CONFIG_DIR = pathlib.Path(__file__).resolve().parent       # .../desktop-client/charter_client
CLIENT_DIR = _CONFIG_DIR.parent                             # .../desktop-client
REPO_ROOT = CLIENT_DIR.parent                               # repo root


# ---------------------------------------------------------------------------
# Early env load — desktop-client/.env first, then agent/.env as fallback.
# Only sets keys absent from the real environment, so shell vars always win.
# ---------------------------------------------------------------------------
def _load_env_file(path: "pathlib.Path") -> None:
    if not path.is_file():
        return
    try:
        for _eline in path.read_text(encoding="utf-8").splitlines():
            _eline = _eline.strip()
            if not _eline or _eline.startswith("#"):
                continue
            _ek, _, _ev = _eline.partition("=")
            _ek = _ek.strip()
            _ev = _ev.strip().strip('"').strip("'")
            if _ek and _ek not in os.environ:
                os.environ[_ek] = _ev
    except Exception:  # noqa: BLE001
        pass


_load_env_file(CLIENT_DIR / ".env")               # desktop-client/.env
_load_env_file(REPO_ROOT / "agent" / ".env")      # agent/.env

# ---------------------------------------------------------------------------
# Background auto-poll configuration
# ---------------------------------------------------------------------------
# Interval between autonomous check-and-continue cycles (in minutes).
# Override via CHARTER_POLL_INTERVAL_MINS in agent/.env or as a shell env var.
# Example for fast testing:  CHARTER_POLL_INTERVAL_MINS=2
_POLL_INTERVAL_MINS: int = int(os.environ.get("CHARTER_POLL_INTERVAL_MINS", "30"))
# Set CHARTER_POLL_BIZ_HOURS_ONLY=1 to restrict polling to Mon-Fri 07:00-20:00 local.
_POLL_BIZ_HOURS_ONLY: bool = os.environ.get("CHARTER_POLL_BIZ_HOURS_ONLY", "0") == "1"

# Base directory for skill definitions.
_SKILLS_BASE_DIR = REPO_ROOT / "agent" / "skills"


def _skill_background_sync(skill_name: str) -> bool:
    """Return True if the skill's SKILL.md declares background_sync: true."""
    if not skill_name:
        return False
    md = _SKILLS_BASE_DIR / skill_name / "SKILL.md"
    try:
        text = md.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.index("---", 3)
            for line in text[3:end].splitlines():
                if "background_sync" in line:
                    val = line.split(":", 1)[1].strip().lower()
                    return val in ("true", "1", "yes")
    except Exception:  # noqa: BLE001
        pass
    return False


# Skill-agnostic check prompt — works regardless of which skill is active.
# The agent reads the skill from the preamble and decides what to do.
_POLL_CHECK_PROMPT = (
    "Check and continue the agent loop. Poll every owner's reply surface for "
    "new submissions since the last pass, classify them, update task statuses, "
    "and give me the digest with any recommended next actions."
)


def _load_agent_env() -> None:
    """No-op: both .env files are now loaded at module level via _load_env_file."""


def _resolve_hosted_url() -> str:
    """Resolve hosted /responses URL from explicit env or construct from FOUNDRY_PROJECT_ENDPOINT."""
    explicit = os.environ.get("AGENT_ENDPOINT_HOSTED") or os.environ.get("AGENT_RESPONSES_URL")
    if explicit:
        return explicit.strip()
    project_endpoint = (os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or "").strip().rstrip("/")
    if not project_endpoint:
        return ""
    agent_name = os.environ.get("AGENT_NAME") or DEFAULT_HOSTED_AGENT_NAME
    return f"{project_endpoint}/agents/{agent_name}/endpoint/protocols/openai/responses?api-version=v1"


def _resolve_project_endpoint() -> str:
    """Resolve the Foundry *project* endpoint used by the SDK client.

    This is the `.../api/projects/<project>` base — distinct from the
    `/responses` URL. Prefer FOUNDRY_PROJECT_ENDPOINT; otherwise derive it by
    stripping the `/agents/<name>/endpoint/...` suffix from the hosted URL.
    """
    ep = (os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or "").strip().rstrip("/")
    if ep:
        return ep
    hosted = _resolve_hosted_url()
    m = re.match(r"(?P<base>.*?)/agents/[^/]+/endpoint/protocols/openai/responses", hosted)
    return m.group("base") if m else ""


UI_HTML = CLIENT_DIR / "ui.html"
ASSETS_DIR = CLIENT_DIR / "assets"
APP_ICON_ICO = ASSETS_DIR / "app_icon.ico"
APP_ICON_PNG = ASSETS_DIR / "app_icon.png"
WINDOW_TITLE = "Project Charter Desktop Agent"

# In local dev the agent's sandbox $HOME is a sibling directory:
#   <repo>/agent/.charter-agent-home/
#       projects/<pid>/project_log.json   (per-project state, source of truth)
#       activity.json                     (global agent audit log; NDJSON)
# The client reads these directly to restore the dashboard on project switch /
# app launch without spending a model turn. In hosted mode `AGENT_HOME` can be
# overridden via env to a mounted location.
_AGENT_HOME = pathlib.Path(
    os.environ.get("AGENT_HOME")
    or (REPO_ROOT / "agent" / ".charter-agent-home")
)

AUTH_CACHE_NAME = "charter-agent-desktop"
_APP_HOME = pathlib.Path.home() / ".charter-agent"
_AUTH_RECORD_PATH = _APP_HOME / "auth_records" / f"{AUTH_CACHE_NAME}.json"

logger = logging.getLogger("charter_desktop")

# Optional file logging. The app runs under pythonw.exe (no console), so by
# default logger output goes nowhere. Set CHARTER_CLIENT_LOG=1 to capture
# verbose client logs (including the per-event [sdk]/[bridge] traces used to
# diagnose transport issues) to ~/.charter-agent/client.log.
if os.environ.get("CHARTER_CLIENT_LOG"):
    try:
        _log_path = _APP_HOME / "client.log"
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        _fh = logging.FileHandler(_log_path, encoding="utf-8")
        _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(_fh)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    except Exception:  # noqa: BLE001
        pass

# ---- client-side store paths ---------------------------------------------
#
# Multi-project support. Each project is a separate workspace (its own
# Foundry session, its own $HOME sandbox on the agent side). The client owns
# the list; the agent learns which project is active via a one-line preamble
# the client prepends to every outbound prompt.

_PROJECTS_PATH = _APP_HOME / "projects.json"
# Client-side cache of the latest dashboard payload per (mode, project_id).
# Used to restore the dashboard on app restart for hosted mode, where the
# agent's $HOME lives in a Foundry microVM the client can't read directly.
_VIEW_CACHE_PATH = _APP_HOME / "view_cache.json"
# Append-only NDJSON log of every unique session_id seen, with timestamps.
# Survives project record overwrites — use this to recover a session ID after
# a fork (e.g. to file a Foundry bug report).
_SESSION_LOG_PATH = _APP_HOME / "session_history.ndjson"
# Per-project conversation transcript stored client-side so it survives
# project switches. Scoped by mode so local and hosted histories are separate.
_TRANSCRIPT_DIR = _APP_HOME / "transcripts"
_TRANSCRIPT_MAX_TURNS = 200  # cap at N user/agent turn pairs
