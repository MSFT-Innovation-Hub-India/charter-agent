"""Boot entrypoint: assert env policy, warm runtimes, load skills, start the
Responses-protocol server.

This agent serves the OpenAI-compatible Responses protocol only — `/responses`
+ SSE streaming, multi-turn via `previous_response_id`. The hosting framework
(`agent-framework-foundry-hosting.ResponsesHostServer`) owns conversation
history; we hand it one warm MAF `Agent` whose instructions are the
`sow-response` skill body and whose tools are the WorkIQ Toolbox plus the
agent-side `$HOME` state tools.
"""

from __future__ import annotations

import logging
import os
from typing import cast

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from .observability import ProcessAttributesSpanProcessor
from .runtime import foundry_host, responses_host, skill_loader

log = logging.getLogger("charter_agent")


def _assert_env() -> None:
    required = [
        "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        "FOUNDRY_PROJECT_ENDPOINT",
        "TOOLBOX_NAME",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"boot: missing required env vars: {', '.join(missing)}")


def _enable_tracing() -> None:
    # Server-side GenAI spans are emitted by Foundry automatically once App
    # Insights is connected. We deliberately do NOT call
    # `AIProjectInstrumentor().instrument()` — azure-ai-projects 2.0.1/2.1.0's
    # Responses instrumentor wraps the upstream stream in an `AsyncStreamWrapper`
    # that lacks the `.headers` attribute `agent_framework_foundry`'s streaming
    # consumer reads, crashing every Responses turn with
    # `'AsyncStreamWrapper' object has no attribute 'headers'`. Re-enable once
    # that upstream bug is fixed.
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        cast(TracerProvider, provider).add_span_processor(ProcessAttributesSpanProcessor())


def _boot() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    # Local dev convenience: load agent/.env (sibling of pyproject.toml) if
    # present. In a hosted-agent container the file is absent and the platform
    # supplies env directly. `override=False` — existing env wins.
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    except ImportError:
        pass

    _assert_env()
    _enable_tracing()
    foundry_host.bootstrap()
    skills = skill_loader.load_all()
    log.info("loaded %d skill(s): %s", len(skills), [s.name for s in skills])


def main() -> None:
    _boot()
    log.info("starting Responses protocol server")
    responses_host.start()


if __name__ == "__main__":
    main()
