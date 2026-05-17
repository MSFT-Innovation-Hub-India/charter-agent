"""Boot entrypoint: assert env policy, warm runtimes, load skills, start server."""

from __future__ import annotations

import logging
import os
from typing import Any, cast

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from .observability import ProcessAttributesSpanProcessor
from .orchestrator import handle_invocation
from .runtime import foundry_host, skill_loader

log = logging.getLogger("charter_agent")


def _assert_env() -> None:
    # Phase 1 + Toolbox wiring: host model + Foundry project + Toolbox name.
    # As Phase 1.5+ modules land, add asserts for GITHUB_TOKEN (codegen sub-agent)
    # and COORDINATOR_OBO_* (when runtime/workiq_token.py is built; until then
    # WorkIQ runs in whatever identity DefaultAzureCredential resolves to —
    # locally, the developer's `az login` user; in production, the agent's
    # Foundry-assigned managed identity, which won't work against WorkIQ until
    # OBO swap is wired).
    required = [
        "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        "FOUNDRY_PROJECT_ENDPOINT",
        "TOOLBOX_NAME",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"boot: missing required env vars: {', '.join(missing)}")


def _enable_tracing() -> None:
    # Opt in to the Foundry SDK's GenAI instrumentation. App Insights export
    # is configured by the platform (auto-injected connection string); we just
    # turn on the instrumentor and attach our per-process attribute injector.
    os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")

    from azure.ai.projects.telemetry import AIProjectInstrumentor

    AIProjectInstrumentor().instrument()

    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        cast(TracerProvider, provider).add_span_processor(ProcessAttributesSpanProcessor())


def _boot() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    # Local dev convenience: load agent/.env if present. In a hosted-agent
    # container the platform supplies env directly; the file simply won't exist.
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass

    _assert_env()
    _enable_tracing()
    foundry_host.bootstrap()
    skills = skill_loader.load_all()
    log.info("loaded %d skill(s): %s", len(skills), [s.name for s in skills])


def main() -> None:
    _boot()

    from azure.ai.agentserver.invocations import InvocationAgentServerHost
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    app = InvocationAgentServerHost()

    @app.invoke_handler
    async def _invoke(request: Request) -> Response:
        body = await request.json()
        action = body.get("action", "echo")
        payload = body.get("payload") or {}

        # The BFF (Phase 5) will populate `visitor` from the MSAL token claims.
        # For now we surface what the platform / dev caller gave us so the
        # orchestrator and handlers see a stable shape.
        visitor: dict[str, Any] = body.get("visitor") or {}
        visitor.setdefault("session_id", request.state.session_id)
        visitor.setdefault("chat_isolation_key", request.state.chat_isolation_key)
        visitor.setdefault("user_isolation_key", request.state.user_isolation_key)

        result = await handle_invocation(action, payload, visitor)
        return JSONResponse(result)

    app.run()


if __name__ == "__main__":
    main()
