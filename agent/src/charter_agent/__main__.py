"""Boot entrypoint: assert env policy, warm runtimes, load skills, start server."""

from __future__ import annotations

import logging
import os
from typing import cast

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
    _assert_env()
    _enable_tracing()
    foundry_host.bootstrap()
    skills = skill_loader.load_all()
    log.info("loaded %d skill(s): %s", len(skills), [s.name for s in skills])


def main() -> None:
    _boot()

    import json

    from azure_ai_agentserver_invocations import InvocationAgentServerHost  # type: ignore[import-not-found]

    app = InvocationAgentServerHost()

    @app.invoke_handler  # type: ignore[misc]
    async def _handle(request):  # type: ignore[no-untyped-def]
        data = await request.json()
        action = data.get("action", "echo")
        payload = data.get("payload", {})
        visitor = data.get("visitor")
        result = await handle_invocation(action, payload, visitor)
        yield f"data: {json.dumps(result)}\n\n"
        yield "event: done\n\n"

    app.run()


if __name__ == "__main__":
    main()
