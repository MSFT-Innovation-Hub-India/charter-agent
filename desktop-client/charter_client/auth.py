"""Authentication: JWT claim decoding, AuthenticationRecord persistence,
interactive-credential construction, and the ``_BridgeTokenCredential`` shim.

``_BridgeTokenCredential`` propagates the END-USER's delegated bearer (not a
service / managed identity) so Foundry's OAuth Identity Passthrough reaches
WorkIQ as the signed-in user — preserving invariant 3.
"""

from __future__ import annotations

import json
import time
from typing import Any

from azure.identity import (
    AuthenticationRecord,
    InteractiveBrowserCredential,
    TokenCachePersistenceOptions,
)

from .config import (
    AUTH_CACHE_NAME,
    TENANT_ID,
    AccessToken,
    InteractiveBrowserBrokerCredential,
    _AUTH_RECORD_PATH,
    _BROKER_AVAILABLE,
    logger,
)


def _decode_jwt_claims(token: str) -> dict:
    """Decode the payload of an Azure AD JWT without verifying the signature."""
    try:
        import base64
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _decode_jwt_name(token: str) -> str | None:
    """Pull a display name out of an Azure AD access token without verifying it.

    We're not validating signature — just extracting claims for UI display. The
    token is still verified end-to-end by the Foundry platform; the client only
    uses it to greet the user. Returns the first of `name`,
    `preferred_username`, `upn`, or `unique_name` that's present.
    """
    claims = _decode_jwt_claims(token)
    for key in ("name", "preferred_username", "upn", "unique_name"):
        val = claims.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _decode_jwt_upn(token: str) -> str | None:
    """Pull the UPN/email out of an Azure AD access token without verifying it."""
    claims = _decode_jwt_claims(token)
    for key in ("preferred_username", "upn", "unique_name"):
        val = claims.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _decode_jwt_oid(token: str) -> str | None:
    """Pull the Entra ID Object ID out of an Azure AD access token.

    The `oid` claim is an immutable GUID that uniquely identifies the user
    regardless of UPN aliases or renames. Used for owner identity matching
    in the dashboard — more reliable than UPN string comparison.
    """
    claims = _decode_jwt_claims(token)
    val = claims.get("oid")
    return val if isinstance(val, str) and val else None


def _load_record() -> AuthenticationRecord | None:
    """Read the saved `AuthenticationRecord` from disk, if any."""
    if not _AUTH_RECORD_PATH.exists():
        return None
    try:
        return AuthenticationRecord.deserialize(_AUTH_RECORD_PATH.read_text(encoding="utf-8"))
    except Exception as ex:  # noqa: BLE001
        logger.warning("auth: failed to load record (%s); will re-prompt.", ex)
        return None


def _save_record(record: AuthenticationRecord) -> None:
    """Persist the `AuthenticationRecord` so future launches are silent."""
    _AUTH_RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    _AUTH_RECORD_PATH.write_text(record.serialize(), encoding="utf-8")


def _build_credential(parent_hwnd: int = 0, *, silent_only: bool = False) -> Any:
    """Build the interactive credential used for sign-in.

    Prefers `InteractiveBrowserBrokerCredential` (Windows account picker via
    WAM) when `azure-identity-broker` is installed; otherwise falls back to
    `InteractiveBrowserCredential` (system browser). Both reuse a persisted
    `AuthenticationRecord` to make repeat launches silent.

    `parent_hwnd` should be the HWND of the foreground app window. WAM
    cancels the request immediately when the handle is 0/invalid, so we
    fall back to `GetForegroundWindow()` and then to the browser credential
    when no usable handle is available.

    When `silent_only=True`, the credential is built with
    `disable_automatic_authentication=True` so `get_token` raises rather than
    popping a UI prompt — used for startup auto sign-in.
    """
    record = _load_record()
    cache_opts = TokenCachePersistenceOptions(name=AUTH_CACHE_NAME)
    common: dict[str, Any] = {
        "cache_persistence_options": cache_opts,
        "authentication_record": record,
    }
    if TENANT_ID:
        common["tenant_id"] = TENANT_ID
    if silent_only:
        common["disable_automatic_authentication"] = True

    if _BROKER_AVAILABLE and InteractiveBrowserBrokerCredential is not None:
        hwnd = parent_hwnd
        if not hwnd:
            try:
                import ctypes
                hwnd = int(ctypes.windll.user32.GetForegroundWindow())
            except Exception:  # noqa: BLE001
                hwnd = 0
        if hwnd:
            return InteractiveBrowserBrokerCredential(parent_window_handle=hwnd, **common)
        logger.warning("auth: no parent HWND available; falling back to browser credential.")
    return InteractiveBrowserCredential(**common)


class _BridgeTokenCredential:
    """Adapts the desktop client's MSAL bearer to azure-core's TokenCredential.

    Critically this propagates the END-USER's delegated token (not a service /
    managed identity), so Foundry's OAuth Identity Passthrough reaches WorkIQ as
    the signed-in user — preserving invariant 3. Do NOT swap this for
    DefaultAzureCredential: that would auth the SDK as a service principal and
    break per-user M365 access.
    """

    def __init__(self, bridge: "Any") -> None:
        self._bridge = bridge

    def get_token(self, *scopes: str, **kwargs: object) -> "AccessToken":  # type: ignore[name-defined]
        b = self._bridge
        # Refresh proactively if the cached bearer is missing or within 60s of expiry.
        if not b.token or time.time() >= (b.token_expires_at - 60):
            b.login()
        return AccessToken(b.token or "", int(b.token_expires_at))
