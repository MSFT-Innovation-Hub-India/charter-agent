"""Tests for the SOW-Owner WorkIQ token provider.

These tests stub MSAL entirely so they run offline; the real OAuth round-trip
is exercised by the live smoke (`scripts/bootstrap_workiq_token.py`, TBD).
"""

from __future__ import annotations

import base64
import json
import sys
import types
from datetime import datetime, timezone
from typing import Any

import pytest

from charter_agent.runtime import workiq_token as wt


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _jwt(claims: dict[str, Any]) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


class _FakeCache:
    def __init__(self) -> None:
        self.has_state_changed = False
        self._payload = ""

    def deserialize(self, s: str) -> None:
        self._payload = s

    def serialize(self) -> str:
        return self._payload


class _FakeConfidentialApp:
    """Replaces ``msal.ConfidentialClientApplication`` for tests."""

    def __init__(self, *, accounts: list[dict[str, Any]], silent_result: Any) -> None:
        self._accounts = accounts
        self._silent_result = silent_result
        self.silent_calls: list[dict[str, Any]] = []

    def get_accounts(self) -> list[dict[str, Any]]:
        return self._accounts

    def acquire_token_silent(self, scopes: list[str], account: dict[str, Any]) -> Any:
        self.silent_calls.append({"scopes": scopes, "account": account})
        return self._silent_result


def _install_fake_msal(monkeypatch: pytest.MonkeyPatch, app: _FakeConfidentialApp) -> _FakeCache:
    """Inject a fake `msal` module into sys.modules for the duration of the test."""
    fake_cache = _FakeCache()
    fake = types.SimpleNamespace(
        SerializableTokenCache=lambda: fake_cache,
        ConfidentialClientApplication=lambda **kwargs: app,
    )
    monkeypatch.setitem(sys.modules, "msal", fake)
    return fake_cache


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOW_OWNER_OBO_TENANT_ID", "tenant-guid")
    monkeypatch.setenv("SOW_OWNER_OBO_CLIENT_ID", "client-guid")
    monkeypatch.setenv("SOW_OWNER_OBO_CLIENT_SECRET", "shh")
    monkeypatch.setenv("WORKIQ_SCOPE", "api://workiq/.default")


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def test_config_from_env_raises_typed_error_when_any_var_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOW_OWNER_OBO_TENANT_ID", "tenant-guid")
    # client id / secret / scope intentionally absent
    with pytest.raises(wt.WorkIQTokenUnavailable) as exc:
        wt.WorkIQTokenConfig.from_env()
    assert exc.value.reason == "config_missing"
    assert "SOW_OWNER_OBO_CLIENT_ID" in exc.value.hint
    assert "WORKIQ_SCOPE" in exc.value.hint


def test_config_from_env_happy(env: None) -> None:
    cfg = wt.WorkIQTokenConfig.from_env()
    assert cfg.tenant_id == "tenant-guid"
    assert cfg.client_id == "client-guid"
    assert cfg.client_secret == "shh"
    assert cfg.scope == "api://workiq/.default"


# --------------------------------------------------------------------------
# acquire — failure modes
# --------------------------------------------------------------------------


def test_acquire_raises_bootstrap_missing_when_cache_has_no_account(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _FakeConfidentialApp(accounts=[], silent_result=None)
    _install_fake_msal(monkeypatch, app)

    provider = wt.WorkIQTokenProvider()
    with pytest.raises(wt.WorkIQTokenUnavailable) as exc:
        provider.acquire()
    assert exc.value.reason == "bootstrap_missing"


def test_acquire_raises_refresh_failed_when_silent_returns_none(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _FakeConfidentialApp(accounts=[{"username": "owner@x"}], silent_result=None)
    _install_fake_msal(monkeypatch, app)

    provider = wt.WorkIQTokenProvider()
    with pytest.raises(wt.WorkIQTokenUnavailable) as exc:
        provider.acquire()
    assert exc.value.reason == "refresh_failed"


def test_acquire_classifies_ca_block_via_aadsts_code(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _FakeConfidentialApp(
        accounts=[{"username": "owner@x"}],
        silent_result={
            "error": "interaction_required",
            "error_description": "AADSTS53000: Conditional Access policy blocked.",
        },
    )
    _install_fake_msal(monkeypatch, app)

    provider = wt.WorkIQTokenProvider()
    with pytest.raises(wt.WorkIQTokenUnavailable) as exc:
        provider.acquire()
    assert exc.value.reason == "ca_blocked"


def test_acquire_classifies_generic_auth_failed(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _FakeConfidentialApp(
        accounts=[{"username": "owner@x"}],
        silent_result={"error": "invalid_grant", "error_description": "AADSTS70008: expired"},
    )
    _install_fake_msal(monkeypatch, app)

    provider = wt.WorkIQTokenProvider()
    with pytest.raises(wt.WorkIQTokenUnavailable) as exc:
        provider.acquire()
    assert exc.value.reason == "auth_failed"


def test_acquire_raises_token_malformed_when_jwt_unparseable(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _FakeConfidentialApp(
        accounts=[{"username": "owner@x"}],
        silent_result={"access_token": "not-a-jwt", "expires_in": 3600},
    )
    _install_fake_msal(monkeypatch, app)

    provider = wt.WorkIQTokenProvider()
    with pytest.raises(wt.WorkIQTokenUnavailable) as exc:
        provider.acquire()
    assert exc.value.reason == "token_malformed"


def test_acquire_raises_token_malformed_when_upn_or_oid_missing(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _jwt({"sub": "anon"})  # neither upn nor oid
    app = _FakeConfidentialApp(
        accounts=[{"username": "owner@x"}],
        silent_result={"access_token": token, "expires_in": 3600},
    )
    _install_fake_msal(monkeypatch, app)

    provider = wt.WorkIQTokenProvider()
    with pytest.raises(wt.WorkIQTokenUnavailable) as exc:
        provider.acquire()
    assert exc.value.reason == "token_malformed"


# --------------------------------------------------------------------------
# acquire — happy path
# --------------------------------------------------------------------------


def test_acquire_returns_token_with_parsed_claims(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _jwt({"upn": "owner@example.com", "oid": "owner-oid-guid"})
    app = _FakeConfidentialApp(
        accounts=[{"username": "owner@example.com"}],
        silent_result={"access_token": token, "expires_in": 3600},
    )
    _install_fake_msal(monkeypatch, app)

    provider = wt.WorkIQTokenProvider()
    result = provider.acquire()

    assert isinstance(result, wt.WorkIQToken)
    assert result.value == token
    assert result.upn == "owner@example.com"
    assert result.oid == "owner-oid-guid"
    assert result.expires_at > datetime.now(timezone.utc)
    assert app.silent_calls == [
        {"scopes": ["api://workiq/.default"], "account": {"username": "owner@example.com"}}
    ]


def test_acquire_falls_back_to_preferred_username_when_upn_absent(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _jwt({"preferred_username": "owner@example.com", "oid": "owner-oid-guid"})
    app = _FakeConfidentialApp(
        accounts=[{"username": "owner@example.com"}],
        silent_result={"access_token": token, "expires_in": 3600},
    )
    _install_fake_msal(monkeypatch, app)

    provider = wt.WorkIQTokenProvider()
    result = provider.acquire()
    assert result.upn == "owner@example.com"


# --------------------------------------------------------------------------
# cache persistence
# --------------------------------------------------------------------------


def test_acquire_persists_cache_when_msal_marks_it_dirty(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token = _jwt({"upn": "owner@example.com", "oid": "owner-oid-guid"})
    app = _FakeConfidentialApp(
        accounts=[{"username": "owner@example.com"}],
        silent_result={"access_token": token, "expires_in": 3600},
    )
    fake_cache = _install_fake_msal(monkeypatch, app)
    fake_cache._payload = "serialised-cache-blob"
    fake_cache.has_state_changed = True  # MSAL flips this after a refresh

    provider = wt.WorkIQTokenProvider()
    provider.acquire()

    persisted = (tmp_path / wt.CACHE_REL_PATH).read_text(encoding="utf-8")
    assert persisted == "serialised-cache-blob"


def test_acquire_does_not_write_cache_when_clean(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token = _jwt({"upn": "owner@example.com", "oid": "owner-oid-guid"})
    app = _FakeConfidentialApp(
        accounts=[{"username": "owner@example.com"}],
        silent_result={"access_token": token, "expires_in": 3600},
    )
    fake_cache = _install_fake_msal(monkeypatch, app)
    fake_cache.has_state_changed = False

    provider = wt.WorkIQTokenProvider()
    provider.acquire()

    assert not (tmp_path / wt.CACHE_REL_PATH).exists()


def test_provider_loads_existing_cache_from_home(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    seeded = "prior-cache-blob"
    (tmp_path / wt.CACHE_REL_PATH).write_text(seeded, encoding="utf-8")

    token = _jwt({"upn": "owner@example.com", "oid": "owner-oid-guid"})
    app = _FakeConfidentialApp(
        accounts=[{"username": "owner@example.com"}],
        silent_result={"access_token": token, "expires_in": 3600},
    )
    fake_cache = _install_fake_msal(monkeypatch, app)

    provider = wt.WorkIQTokenProvider()
    provider.acquire()

    assert fake_cache._payload == seeded
