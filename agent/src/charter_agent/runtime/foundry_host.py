"""Sole owner of the warm `FoundryChatClient`, the Foundry Toolbox MCP tool,
and one warm MAF `Agent` per loaded skill bundle.

Mirrors the canonical hosted-agent sample
`microsoft-foundry/foundry-samples/.../responses/04-foundry-toolbox/main.py`:
the Toolbox is attached as a raw `MCPStreamableHTTPTool` backed by an
`httpx.AsyncClient` whose `auth` injects a fresh
`https://ai.azure.com/.default` bearer on every outbound MCP request. The
mandatory `Foundry-Features: Toolboxes=V1Preview` header is set on the
client's default headers so it ships with every request (including the
initial MCP `initialize` / `tools/list`, which `header_provider` does not
cover).

Per-bundle Agents: at boot we call `skill_loader.load_bundles()` and
construct one warm `Agent` per skill, attaching its `inprocess_tools` plus a
toolbox handle. When the bundle declares a literal `toolbox_allowlist` we
build a per-skill `MCPStreamableHTTPTool` with `allowed_tools=` set so the
toolbox surface is filtered at the MCP layer; otherwise we reuse the shared
unrestricted `_mcp_tool`.

Enforced by `import-linter`: this module is the only place allowed to import
from `agent_framework` / `agent_framework_foundry`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..state import home_dir
from . import skill_loader
from .state_tools import STATE_TOOLS

_chat_client: Any | None = None
_mcp_tool: Any | None = None
_credential: Any | None = None
_http_client: Any | None = None
_token_provider: Any | None = None
_toolbox_url: str | None = None
_toolbox_name: str | None = None
_sessions: dict[str, Any] = {}
_agents: dict[str, Any] = {}


@dataclass(frozen=True)
class HostConfig:
    deployment_name: str
    project_endpoint: str
    toolbox_name: str
    toolbox_version: str | None  # None → consumer endpoint (prod)


def _read_config() -> HostConfig:
    deployment = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not deployment or not endpoint:
        raise RuntimeError(
            "foundry_host: AZURE_AI_MODEL_DEPLOYMENT_NAME and "
            "FOUNDRY_PROJECT_ENDPOINT must both be set."
        )
    return HostConfig(
        deployment_name=deployment,
        project_endpoint=endpoint,
        toolbox_name=os.environ.get("TOOLBOX_NAME", "Charter-Agent-Tools"),
        toolbox_version=os.environ.get("TOOLBOX_VERSION") or None,
    )


def _build_toolbox_url(cfg: HostConfig) -> str:
    base = cfg.project_endpoint.rstrip("/")
    if cfg.toolbox_version:
        return (
            f"{base}/toolboxes/{cfg.toolbox_name}"
            f"/versions/{cfg.toolbox_version}/mcp?api-version=v1"
        )
    return f"{base}/toolboxes/{cfg.toolbox_name}/mcp?api-version=v1"


class _ToolboxAuth(httpx.Auth):
    """httpx.Auth that mints a fresh Foundry bearer on every request (async)."""

    def __init__(self, token_provider: Any) -> None:
        self._get_token = token_provider

    async def async_auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self._get_token()}"
        yield request


def _make_mcp_tool(allowlist: tuple[str, ...] | None) -> Any:
    """Build an `MCPStreamableHTTPTool` sharing the warm http_client + URL."""
    from agent_framework import MCPStreamableHTTPTool  # type: ignore[import-not-found]

    kwargs: dict[str, Any] = {
        "name": _toolbox_name,
        "url": _toolbox_url,
        "http_client": _http_client,
        "load_prompts": False,
    }
    if allowlist:
        kwargs["allowed_tools"] = list(allowlist)
    return MCPStreamableHTTPTool(**kwargs)


def _build_agent(bundle: skill_loader.SkillBundle) -> Any:
    """Construct a warm `Agent` for a bundle with the right tool surface."""
    from agent_framework import Agent  # type: ignore[import-not-found]

    if bundle.toolbox_allowlist:
        toolbox = _make_mcp_tool(bundle.toolbox_allowlist)
    else:
        toolbox = _mcp_tool  # shared unrestricted

    return Agent(
        client=_chat_client,
        instructions=bundle.manifest.body,
        tools=[toolbox, *bundle.inprocess_tools],
        default_options={"store": False},
    )


def bootstrap() -> None:
    """Instantiate the warm `FoundryChatClient`, the Toolbox MCP tool, and one
    warm `Agent` per loaded skill bundle. Idempotent."""
    global _chat_client, _mcp_tool, _credential, _http_client, _token_provider
    global _toolbox_url, _toolbox_name
    if _chat_client is not None:
        return

    cfg = _read_config()

    from agent_framework_foundry import FoundryChatClient  # type: ignore[import-not-found]
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    _credential = DefaultAzureCredential()
    _token_provider = get_bearer_token_provider(
        _credential, "https://ai.azure.com/.default"
    )

    _toolbox_url = _build_toolbox_url(cfg)
    _toolbox_name = cfg.toolbox_name

    _http_client = httpx.AsyncClient(
        auth=_ToolboxAuth(_token_provider),
        headers={"Foundry-Features": "Toolboxes=V1Preview"},
        timeout=120.0,
    )

    _mcp_tool = _make_mcp_tool(None)

    _chat_client = FoundryChatClient(
        project_endpoint=cfg.project_endpoint,
        model=cfg.deployment_name,
        credential=_credential,
    )

    (home_dir() / "agent_session").mkdir(parents=True, exist_ok=True)

    # Register shared in-process tools, then build one warm Agent per bundle.
    skill_loader.register_tools(list(STATE_TOOLS))
    bundles = skill_loader.load_bundles()
    _agents.clear()
    for name, bundle in bundles.items():
        _agents[name] = _build_agent(bundle)


def get_chat_agent() -> Any:
    """Backward-compatible accessor returning the warm `FoundryChatClient`."""
    if _chat_client is None:
        raise RuntimeError("foundry_host.bootstrap() must be called first.")
    return _chat_client


def get_toolbox() -> Any:
    """Return the shared unrestricted Toolbox MCP tool."""
    if _mcp_tool is None:
        raise RuntimeError("foundry_host.bootstrap() must be called first.")
    return _mcp_tool


def get_agent(skill_name: str) -> Any:
    """Return the warm `Agent` built for `skill_name`."""
    if not _agents:
        raise RuntimeError("foundry_host.bootstrap() must be called first.")
    try:
        return _agents[skill_name]
    except KeyError as e:
        raise KeyError(
            f"foundry_host: no agent for skill {skill_name!r} "
            f"(have {list(_agents)})"
        ) from e


def get_session(session_id: str) -> Any:
    """Resume the MAF `AgentSession` for `session_id`, or create one and persist it."""
    if _chat_client is None:
        raise RuntimeError("foundry_host.bootstrap() must be called before get_session().")

    if session_id in _sessions:
        return _sessions[session_id]

    session_file = home_dir() / "agent_session" / f"{session_id}.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    resumed = session_file.exists()
    if not resumed:
        session_file.write_text("{}", encoding="utf-8")
    thread = {"resumed": resumed, "path": str(session_file)}

    _sessions[session_id] = thread
    return thread


def session_path(session_id: str) -> Path:
    return home_dir() / "agent_session" / f"{session_id}.json"


async def run_skill(
    *,
    user_prompt: str,
    skill_name: str = "sow-response",
    session_id: str | None = None,
) -> Any:
    """Run a one-shot turn against the warm Agent for `skill_name`.

    Used by smoke scripts. The production path is `responses_host.start()`,
    which fetches the same warm Agent via `get_agent(...)`.
    """
    return await get_agent(skill_name).run(user_prompt)
