"""Sole owner of the warm `FoundryChatClient` and the Foundry Toolbox MCP tool.

Mirrors the canonical hosted-agent sample
`microsoft-foundry/foundry-samples/.../responses/04-foundry-toolbox/main.py`:
the Toolbox is attached as a raw `MCPStreamableHTTPTool` backed by an
`httpx.AsyncClient` whose `auth` injects a fresh
`https://ai.azure.com/.default` bearer on every outbound MCP request. The
mandatory `Foundry-Features: Toolboxes=V1Preview` header is set on the
client's default headers so it ships with every request (including the
initial MCP `initialize` / `tools/list`, which `header_provider` does not
cover). `get_mcp_tool(...)` is not used because it cannot mint per-call
bearers — the Toolbox URL sits behind APIM and returns 401 without one.

Auth:
- `FoundryChatClient` uses `DefaultAzureCredential` for its outbound calls
  to the Foundry project (the hosted agent's identity — project MI before
  publish, agent MI after; the developer's `az login` identity locally).
- The Toolbox MCP tool uses the same credential via
  `get_bearer_token_provider`.

Enforced by `import-linter`: this module is the only place allowed to import
from `agent_framework` / `agent_framework_foundry`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..state import home_dir

_chat_client: Any | None = None
_mcp_tool: Any | None = None
_credential: Any | None = None
_sessions: dict[str, Any] = {}


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



import httpx

class _ToolboxAuth(httpx.Auth):
    """httpx.Auth that mints a fresh Foundry bearer on every request (async)."""
    def __init__(self, token_provider: Any) -> None:
        self._get_token = token_provider

    async def async_auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self._get_token()}"
        yield request


def bootstrap() -> None:
    """Instantiate the warm `FoundryChatClient` and the Toolbox MCP tool. Idempotent."""
    global _chat_client, _mcp_tool, _credential
    if _chat_client is not None:
        return

    cfg = _read_config()

    import httpx

    from agent_framework import MCPStreamableHTTPTool  # type: ignore[import-not-found]
    from agent_framework_foundry import FoundryChatClient  # type: ignore[import-not-found]
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    _credential = DefaultAzureCredential()

    token_provider = get_bearer_token_provider(
        _credential, "https://ai.azure.com/.default"
    )

    http_client = httpx.AsyncClient(
        auth=_ToolboxAuth(token_provider),
        headers={"Foundry-Features": "Toolboxes=V1Preview"},
        timeout=120.0,
    )

    _mcp_tool = MCPStreamableHTTPTool(
        name=cfg.toolbox_name,
        url=_build_toolbox_url(cfg),
        http_client=http_client,
        load_prompts=False,
    )

    _chat_client = FoundryChatClient(
        project_endpoint=cfg.project_endpoint,
        model=cfg.deployment_name,
        credential=_credential,
    )

    (home_dir() / "agent_session").mkdir(parents=True, exist_ok=True)


def get_chat_agent() -> Any:
    """Backward-compatible accessor returning the warm `FoundryChatClient`."""
    if _chat_client is None:
        raise RuntimeError("foundry_host.bootstrap() must be called first.")
    return _chat_client


def get_toolbox() -> Any:
    """Return the hosted MCP tool spec for the WorkIQ Toolbox."""
    if _mcp_tool is None:
        raise RuntimeError("foundry_host.bootstrap() must be called first.")
    return _mcp_tool


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
    skill_body: str,
    user_prompt: str,
    session_id: str | None = None,
) -> Any:
    """Run a one-shot host-Agent turn using `skill_body` as instructions, with
    the hosted Toolbox MCP tool + agent-side state tools attached.

    Used by smoke scripts. The production path is `responses_host.start()`,
    which constructs the agent once at boot.
    """
    if _chat_client is None or _mcp_tool is None:
        raise RuntimeError("foundry_host.bootstrap() must be called first.")

    from agent_framework import Agent  # type: ignore[import-not-found]

    from .state_tools import STATE_TOOLS

    agent = Agent(
        client=_chat_client,
        instructions=skill_body,
        tools=[_mcp_tool, *STATE_TOOLS],
        default_options={"store": False},
    )
    return await agent.run(user_prompt)