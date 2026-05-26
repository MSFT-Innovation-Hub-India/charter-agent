"""Telemetry + audit log.

Tracing wiring is owned by the Foundry SDK and the agent server:
- Server-side traces (root invocation, model call, tool call) are emitted
  automatically by Foundry once the project is connected to App Insights.
- Client-side spans come from `AIProjectInstrumentor().instrument()`, opted
  in via `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true` at boot.
- Custom function spans use the `@trace_function` decorator re-exported below.
- Process-wide span attributes (`project.id`, `gen_ai.conversation.id`) are
  injected by `ProcessAttributesSpanProcessor`, registered once at boot.

The only thing this module owns is `log_activity()` — the `$HOME/activity.json`
NDJSON audit log required by invariant 6 / spec §10.13. That is product
behaviour (the narrative record the dashboard renders), not telemetry.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from azure.ai.projects.telemetry import trace_function
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

__all__ = ["trace_function", "log_activity", "ProcessAttributesSpanProcessor"]


class ProcessAttributesSpanProcessor(SpanProcessor):
    """Stamp every span with the project / session IDs from the agent's env."""

    def on_start(self, span: Span, parent_context=None) -> None:  # type: ignore[override]
        project_id = os.environ.get("FOUNDRY_AGENT_SESSION_ID")
        if project_id:
            span.set_attribute("project.id", project_id)
            span.set_attribute("gen_ai.conversation.id", project_id)

    def on_end(self, span: ReadableSpan) -> None:  # type: ignore[override]
        pass


def log_activity(
    *,
    actor: str,
    kind: str,
    summary: str,
    ref: str | None = None,
) -> None:
    """Append one entry to the per-project audit log (``activity.json``).

    Routes through ``state.append_ndjson`` so all ``$HOME`` I/O goes through
    the single path-containment-checked gateway. Imported lazily to keep the
    dependency one-directional (state → observability is forbidden).
    """
    entry = {
        "at": datetime.now(UTC).isoformat(),
        "actor": actor,
        "kind": kind,
        "summary": summary,
        "ref": ref,
        "span_id": format(trace.get_current_span().get_span_context().span_id, "016x"),
    }
    from . import state  # noqa: PLC0415

    pid = state.active_project_id()
    state.append_ndjson(f"projects/{pid}/activity.json", entry)
