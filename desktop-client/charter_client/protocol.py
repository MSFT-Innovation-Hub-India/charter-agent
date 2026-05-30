"""Streaming-protocol helpers: SSE parsing, consent extraction, dashboard
extraction, and SDK-event normalisation.

These are pure functions with no client-state dependency, shared by both the
raw-httpx and SDK transports so the two paths run identical event semantics.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx


def _iter_sse_events(response: httpx.Response):
    event_name: str | None = None
    data_lines: list[str] = []
    for raw in response.iter_lines():
        line = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                try:
                    parsed: Any = json.loads(payload)
                except json.JSONDecodeError:
                    parsed = {"raw": payload}
                yield {"event": event_name or (parsed.get("type") if isinstance(parsed, dict) else "message"), "data": parsed}
            event_name = None
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())


def _extract_consent_url(payload: dict) -> str | None:
    def walk(obj: Any) -> str | None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and v.startswith("http") and (
                    "consent" in k.lower() or "auth" in k.lower() or "login.microsoftonline" in v
                ):
                    return v
                r = walk(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = walk(item)
                if r:
                    return r
        return None

    return walk(payload)


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.MULTILINE)


def _maybe_extract_dashboard(text: str) -> dict | None:
    for m in _JSON_BLOCK_RE.finditer(text or ""):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("kind") == "dashboard":
            return obj
    return None


def _sdk_event_to_dict(event: object) -> dict:
    """Normalise an OpenAI/Foundry streaming event into a plain dict.

    The SDK yields typed pydantic event models. We funnel them through the same
    dict-shaped switch the raw-httpx path uses, so the two transports share the
    exact same event-handling semantics.
    """
    if isinstance(event, dict):
        return event
    for attr in ("model_dump", "to_dict"):
        fn = getattr(event, attr, None)
        if callable(fn):
            try:
                d = fn()
                if isinstance(d, dict):
                    return d
            except Exception:  # noqa: BLE001
                pass
    # Best-effort fallback for unknown/untyped events (e.g. Foundry-specific
    # oauth_consent_request frames the OpenAI typed union doesn't model).
    out: dict = {}
    for k in ("type", "delta", "arguments", "id", "item", "response", "agent_session_id"):
        v = getattr(event, k, None)
        if v is None:
            continue
        sub = getattr(v, "model_dump", None)
        out[k] = sub() if callable(sub) else v
    return out
