"""SOW-Owner delegated WorkIQ token provider.

Sole source of the SOW-Owner delegated bearer that gets stamped on every
outbound Foundry-Toolbox MCP request (via ``foundry_host``'s auth-injection
hook). Single-user model — there is no per-visitor OBO, no deputy fallback,
no second identity. If a fresh token cannot be acquired, this module raises
``WorkIQTokenUnavailable``; callers must surface that as a coordinator-facing
exception rather than reach for a fallback identity (invariant 3).

Mechanism (despite the "OBO" naming in the spec, which refers to the *posture*,
not the OAuth flow): we run a confidential-client app against the SOW Owner's
home tenant and call ``acquire_token_silent`` against a serialised MSAL token
cache persisted to ``$HOME/.workiq_token_cache.json``. The cache must be
seeded once — by the BFF's MSAL auth-code flow in production (Phase 5), or by
``scripts/bootstrap_workiq_token.py`` (device-code) in dev. The agent itself
never runs an interactive flow.

Required environment variables:
    SOW_OWNER_OBO_TENANT_ID       — SOW Owner's home tenant GUID
    SOW_OWNER_OBO_CLIENT_ID       — confidential-client app registration GUID
    SOW_OWNER_OBO_CLIENT_SECRET   — confidential-client secret
    WORKIQ_SCOPE                  — e.g. ``api://<workiq-app-id>/.default``
"""

from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import workiq_token_cache  # local helper, see bottom of file


CACHE_REL_PATH = ".workiq_token_cache.json"


class WorkIQTokenUnavailable(RuntimeError):
    """Raised when a SOW-Owner WorkIQ token cannot be obtained.

    ``reason`` is a short machine-readable tag for telemetry / dashboard
    classification; ``hint`` is the human-facing remediation string surfaced
    to the coordinator. There is no fallback identity — callers must surface
    this as an exception and stop.
    """

    def __init__(self, reason: str, hint: str, *, inner: BaseException | None = None) -> None:
        self.reason = reason
        self.hint = hint
        super().__init__(f"[{reason}] {hint}")
        if inner is not None:
            self.__cause__ = inner


@dataclass(frozen=True)
class WorkIQToken:
    value: str
    expires_at: datetime  # UTC
    upn: str
    oid: str


@dataclass(frozen=True)
class WorkIQTokenConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    scope: str

    @classmethod
    def from_env(cls) -> WorkIQTokenConfig:
        missing = [
            name
            for name in (
                "SOW_OWNER_OBO_TENANT_ID",
                "SOW_OWNER_OBO_CLIENT_ID",
                "SOW_OWNER_OBO_CLIENT_SECRET",
                "WORKIQ_SCOPE",
            )
            if not os.environ.get(name)
        ]
        if missing:
            raise WorkIQTokenUnavailable(
                "config_missing",
                f"Missing required env vars: {', '.join(missing)}.",
            )
        return cls(
            tenant_id=os.environ["SOW_OWNER_OBO_TENANT_ID"],
            client_id=os.environ["SOW_OWNER_OBO_CLIENT_ID"],
            client_secret=os.environ["SOW_OWNER_OBO_CLIENT_SECRET"],
            scope=os.environ["WORKIQ_SCOPE"],
        )


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    try:
        _h, payload, _s = token.split(".")
    except ValueError as e:
        raise WorkIQTokenUnavailable(
            "token_malformed", "Acquired token is not a JWT (cannot extract upn/oid)."
        ) from e
    padded = payload + "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, UnicodeDecodeError) as e:
        raise WorkIQTokenUnavailable(
            "token_malformed", "JWT payload is not valid base64-url JSON."
        ) from e


class WorkIQTokenProvider:
    """Thread-safe SOW-Owner WorkIQ token acquirer.

    One instance per process. ``acquire()`` is sync (MSAL is sync); call it
    from inside the async auth-injection hook via ``asyncio.to_thread``.
    """

    def __init__(self, cfg: WorkIQTokenConfig | None = None) -> None:
        self._cfg = cfg or WorkIQTokenConfig.from_env()
        self._lock = threading.Lock()
        self._app: Any | None = None
        self._cache: Any | None = None

    def _ensure_app(self) -> Any:
        if self._app is not None:
            return self._app
        try:
            import msal  # type: ignore[import-not-found]
        except ImportError as e:
            raise WorkIQTokenUnavailable(
                "msal_missing",
                "Install the `msal` dependency (it ships in agent/pyproject.toml).",
                inner=e,
            ) from e

        self._cache = msal.SerializableTokenCache()
        serialised = workiq_token_cache.load(CACHE_REL_PATH)
        if serialised:
            self._cache.deserialize(serialised)

        self._app = msal.ConfidentialClientApplication(
            client_id=self._cfg.client_id,
            client_credential=self._cfg.client_secret,
            authority=f"https://login.microsoftonline.com/{self._cfg.tenant_id}",
            token_cache=self._cache,
        )
        return self._app

    def _persist_cache_if_dirty(self) -> None:
        if self._cache is not None and self._cache.has_state_changed:
            workiq_token_cache.save(CACHE_REL_PATH, self._cache.serialize())

    def acquire(self) -> WorkIQToken:
        with self._lock:
            app = self._ensure_app()
            accounts = app.get_accounts()
            if not accounts:
                raise WorkIQTokenUnavailable(
                    "bootstrap_missing",
                    "No SOW-Owner account in the token cache. Seed it once via the "
                    "BFF MSAL flow (Phase 5) or `scripts/bootstrap_workiq_token.py`.",
                )
            result = app.acquire_token_silent(scopes=[self._cfg.scope], account=accounts[0])
            self._persist_cache_if_dirty()

            if result is None:
                raise WorkIQTokenUnavailable(
                    "refresh_failed",
                    "MSAL returned no token. The cached refresh token is likely expired or "
                    "revoked; the SOW Owner must re-authenticate via the BFF.",
                )
            if "error" in result:
                err = result.get("error")
                desc = result.get("error_description", "")
                reason = "ca_blocked" if "AADSTS50158" in desc or "AADSTS53000" in desc else "auth_failed"
                raise WorkIQTokenUnavailable(reason, f"{err}: {desc}")

            access_token = result.get("access_token")
            expires_in = int(result.get("expires_in", 0))
            if not access_token:
                raise WorkIQTokenUnavailable(
                    "auth_failed", "MSAL response missing access_token."
                )

            claims = _decode_jwt_claims(access_token)
            upn = claims.get("upn") or claims.get("preferred_username") or ""
            oid = claims.get("oid") or ""
            if not upn or not oid:
                raise WorkIQTokenUnavailable(
                    "token_malformed",
                    "Acquired token is missing required upn/oid claims.",
                )

            return WorkIQToken(
                value=access_token,
                expires_at=datetime.now(timezone.utc).replace(microsecond=0)
                + _seconds(expires_in),
                upn=upn,
                oid=oid,
            )


def _seconds(n: int):
    from datetime import timedelta

    return timedelta(seconds=max(n, 0))
