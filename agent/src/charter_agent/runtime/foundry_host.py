"""Sole owner of the MAF host `ChatAgent`, its per-session `AgentSession` threads,
and the Foundry Toolbox (`Charter-Agent-Tools`) attached as the WorkIQ tool surface.

The host model is wired via `agent_framework_foundry.FoundryChatClient` (the
released successor to the older `AzureAIAgentClient` — see AGENTS.md §4 / §11.9).
The Toolbox is attached as a raw `MCPStreamableHTTPTool` against the Foundry
Toolbox MCP endpoint; there is no `AzureAIProjectToolbox`-style wrapper in any
released MAF package, and the architectural intent (AGENTS.md §4.1) has always
been "call the Toolbox over MCP." The mandatory `Foundry-Features:
Toolboxes=V1Preview` header and the auth token are stamped on every outbound
request by a `header_provider` callback.

Auth:
- Host model + Toolbox connection: `DefaultAzureCredential` against
  `https://ai.azure.com/.default` (Foundry-assigned Managed Identity in
  production; the developer's `az login` identity locally).
- WorkIQ servers behind the Toolbox: per invariant 3 require the coordinator's
  delegated token. In dev this is naturally the same identity (you are the
  coordinator). Production OBO swap lands when `runtime/workiq_token.py` is built.

Enforced by `import-linter`: this module is the only place allowed to import from
`agent_framework` / `agent_framework_foundry`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..state import home_dir

_chat_agent: Any | None = None
_toolbox: Any | None = None
_sessions: dict[str, Any] = {}

_FOUNDRY_SCOPE = "https://ai.azure.com/.default"


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


_credential: Any | None = None


def _build_toolbox_url(cfg: HostConfig) -> str:
    base = cfg.project_endpoint.rstrip("/")
    if cfg.toolbox_version:
        return f"{base}/toolboxes/{cfg.toolbox_name}/versions/{cfg.toolbox_version}/mcp?api-version=v1"
    return f"{base}/toolboxes/{cfg.toolbox_name}/mcp?api-version=v1"


def _toolbox_headers() -> dict[str, str]:
    """Build mandatory headers + bearer token for a Toolbox MCP request.

    Stamps the mandatory `Foundry-Features: Toolboxes=V1Preview` header and a
    bearer. The token currently comes from `DefaultAzureCredential` (in dev
    that resolves to the coordinator's `az login` identity, satisfying
    invariant 3 incidentally). Production swap to `runtime/workiq_token.py`
    lands when that module is built.
    """
    cred = _credential
    if cred is None:
        raise RuntimeError("foundry_host.bootstrap() must be called first.")
    token = cred.get_token(_FOUNDRY_SCOPE).token
    return {
        "Authorization": f"Bearer {token}",
        "Foundry-Features": "Toolboxes=V1Preview",
    }


def _toolbox_header_provider(_existing: dict[str, Any]) -> dict[str, str]:
    """Per-tool-call header injector (only fires on `call_tool`, not connect)."""
    return _toolbox_headers()


def _build_toolbox_http_client() -> Any:
    """`httpx.AsyncClient` that stamps auth headers on **every** outbound
    request — including the `initialize` and `tools/list` calls that MAF's
    `header_provider` hook bypasses (see `_mcp.py` ~L1598: hook only set up
    inside `call_tool`).
    """
    from httpx import AsyncClient, Request, Timeout

    async def _inject(request: Request) -> None:  # noqa: RUF029
        for k, v in _toolbox_headers().items():
            request.headers[k] = v

    return AsyncClient(
        follow_redirects=True,
        timeout=Timeout(90.0, read=90.0),
        event_hooks={"request": [_inject]},
    )


def bootstrap() -> None:
    """Instantiate the warm host `FoundryChatClient` and attach the Toolbox MCP tool. Idempotent."""
    global _chat_agent, _toolbox, _credential
    if _chat_agent is not None:
        return

    cfg = _read_config()

    # Late imports keep the import-linter contract local to this module.
    from agent_framework import MCPStreamableHTTPTool  # type: ignore[import-not-found]
    from agent_framework_foundry import FoundryChatClient  # type: ignore[import-not-found]
    from azure.identity import DefaultAzureCredential

    _credential = DefaultAzureCredential()

    _chat_agent = FoundryChatClient(
        project_endpoint=cfg.project_endpoint,
        model=cfg.deployment_name,
        credential=_credential,
    )

    # Raw MCP against the Foundry Toolbox endpoint is the production path
    # (AGENTS.md §4.1). `header_provider` injects the bearer + `Foundry-Features`
    # header on every outbound call. Approval gating lives at the
    # SuggestedAction layer (`actions/`), not at MCP, so we set
    # `approval_mode="never_require"`.
    _toolbox = MCPStreamableHTTPTool(
        name="workiq",
        url=_build_toolbox_url(cfg),
        header_provider=_toolbox_header_provider,
        http_client=_build_toolbox_http_client(),
        approval_mode="never_require",
        request_timeout=90,
        load_prompts=False,
    )

    (home_dir() / "agent_session").mkdir(parents=True, exist_ok=True)


def get_chat_agent() -> Any:
    if _chat_agent is None:
        raise RuntimeError("foundry_host.bootstrap() must be called first.")
    return _chat_agent


def get_toolbox() -> Any:
    if _toolbox is None:
        raise RuntimeError("foundry_host.bootstrap() must be called first.")
    return _toolbox


def get_session(session_id: str) -> Any:
    """Resume the MAF `AgentSession` for `session_id`, or create one and persist it."""
    if _chat_agent is None:
        raise RuntimeError("foundry_host.bootstrap() must be called before get_session().")

    if session_id in _sessions:
        return _sessions[session_id]

    session_file = home_dir() / "agent_session" / f"{session_id}.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    # Phase 1: minimal placeholder — the real MAF AgentSession thread will be wired
    # in Phase 2 once a skill actually invokes the model. For now we just record
    # that the session has been touched, to demonstrate $HOME-backed continuity.
    if session_file.exists():
        thread = {"resumed": True, "path": str(session_file)}
    else:
        session_file.write_text("{}", encoding="utf-8")
        thread = {"resumed": False, "path": str(session_file)}

    _sessions[session_id] = thread
    return thread


def session_path(session_id: str) -> Path:
    return home_dir() / "agent_session" / f"{session_id}.json"


async def run_skill(
    *,
    skill_body: str,
    user_prompt: str,
    response_format: type | None = None,
    session_id: str | None = None,
) -> Any:
    """Run a one-shot ChatAgent turn using `skill_body` as instructions and the
    Toolbox attached as the tool surface.

    Returns the MAF run result. If `response_format` is a Pydantic model class,
    MAF is asked to produce structured output conforming to it; the caller is
    responsible for parsing `result` into that model (MAF surfaces vary slightly
    across `agent-framework` versions).
    """
    if _chat_agent is None or _toolbox is None:
        raise RuntimeError("foundry_host.bootstrap() must be called first.")

    from agent_framework import ChatAgent  # type: ignore[import-not-found]

    agent = ChatAgent(
        chat_client=_chat_agent,
        instructions=skill_body,
        tools=[_toolbox],
    )
    kwargs: dict[str, Any] = {}
    if response_format is not None:
        kwargs["response_format"] = response_format
    return await agent.run(user_prompt, **kwargs)


async def call_toolbox_tool(tool_name: str, args: dict[str, Any]) -> Any:
    """Directly invoke a tool on the attached Toolbox by name.

    Used for deterministic kickoff/fan-out side-effects where wrapping an LLM
    call around each WorkIQ operation would be wasteful. `MCPStreamableHTTPTool.call_tool`
    takes the tool name plus kwargs (not a dict), so we unpack here.
    """
    if _toolbox is None:
        raise RuntimeError("foundry_host.bootstrap() must be called first.")
    return await _toolbox.call_tool(tool_name, **args)

