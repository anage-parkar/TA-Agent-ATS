"""Startup auth guard + security events.

Proves: the app refuses to start with trusted X-User-* headers outside a
dev-like APP_ENV unless explicitly overridden (and the override is recorded as
a critical security event); the guard is wired into the app lifespan; and any
request that tries to claim the reserved system role or `system:` identity is
recorded as a structured security event without breaking the request path.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from services import security_events
from services.auth import (
    DEV_LIKE_ENVS,
    InsecureAuthConfig,
    _resolve_principal,
    assert_safe_auth_config,
)
from services.config import settings


@pytest.fixture(autouse=True)
def events():
    """Capture every security event emitted during the test."""
    security_events.clear_sinks()
    captured: list[dict] = []
    security_events.register_sink(captured.append)
    yield captured
    security_events.clear_sinks()


def _cfg(monkeypatch, *, app_env, trust=True, override=False):
    monkeypatch.setattr(settings, "app_env", app_env)
    monkeypatch.setattr(settings, "trust_auth_headers", trust)
    monkeypatch.setattr(settings, "allow_trusted_headers_outside_dev", override)


def _request(**headers) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({
        "type": "http", "method": "POST", "path": "/api/x", "headers": raw,
        "query_string": b"", "client": ("203.0.113.9", 1234),
    })


# ── startup guard ─────────────────────────────────────────────────────
@pytest.mark.parametrize("env", sorted(DEV_LIKE_ENVS) + ["Dev", " TEST "])
def test_trusted_headers_allowed_in_dev_like_envs(monkeypatch, env):
    _cfg(monkeypatch, app_env=env)
    assert_safe_auth_config()  # no raise


@pytest.mark.parametrize("env", ["prod", "production", "staging", "uat", ""])
def test_trusted_headers_refused_outside_dev(monkeypatch, env, events):
    _cfg(monkeypatch, app_env=env)
    with pytest.raises(InsecureAuthConfig) as exc:
        assert_safe_auth_config()
    msg = str(exc.value)
    for knob in ("TRUST_AUTH_HEADERS", "APP_ENV", "ALLOW_TRUSTED_HEADERS_OUTSIDE_DEV"):
        assert knob in msg  # the error tells the operator exactly what to change
    assert events == []


def test_unset_app_env_is_treated_as_production(monkeypatch):
    """The out-of-the-box default (no APP_ENV anywhere) must fail closed —
    an operator who forgot to set it gets a refusal, not a silently dev app."""
    from services.config import Settings

    monkeypatch.delenv("APP_ENV", raising=False)
    fresh = Settings(_env_file=None)  # ignore local .env files: pure defaults
    assert fresh.app_env == ""
    assert fresh.trust_auth_headers is True  # the dangerous combination, by default
    with pytest.raises(InsecureAuthConfig) as exc:
        assert_safe_auth_config(fresh)
    assert "unset" in str(exc.value).lower()


@pytest.mark.parametrize("value", ["", "   ", None])
def test_blank_app_env_fails_closed(monkeypatch, value):
    _cfg(monkeypatch, app_env=value)
    with pytest.raises(InsecureAuthConfig):
        assert_safe_auth_config()


def test_prod_with_real_auth_is_fine(monkeypatch):
    _cfg(monkeypatch, app_env="prod", trust=False)
    assert_safe_auth_config()


def test_explicit_override_allows_start_but_records_critical_event(monkeypatch, events, caplog):
    _cfg(monkeypatch, app_env="prod", override=True)
    with caplog.at_level(logging.CRITICAL, logger="ta_agent.security"):
        assert_safe_auth_config()
    ev = [e for e in events if e["event"] == "auth.trusted_headers_override"]
    assert ev and ev[0]["severity"] == "critical" and ev[0]["app_env"] == "prod"
    assert any("auth.trusted_headers_override" in r.getMessage() for r in caplog.records)


def test_app_startup_runs_the_guard(monkeypatch):
    import main

    # Keep startup hermetic: no LLM/DB probes in tests.
    monkeypatch.setattr(main, "_verify_llm", lambda: False)
    monkeypatch.setattr(main, "db_available", lambda: False)

    _cfg(monkeypatch, app_env="prod")
    with pytest.raises(InsecureAuthConfig):
        with TestClient(main.app):  # entering the client runs the lifespan
            pass

    _cfg(monkeypatch, app_env="dev")
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200


# ── security events on identity claims ────────────────────────────────
def test_system_role_claim_is_recorded_and_downgraded(events, caplog):
    with caplog.at_level(logging.WARNING, logger="ta_agent.security"):
        p = _resolve_principal(_request(**{"X-User-Role": " System ", "X-User-Id": "mallory"}))
    assert p.role == "interviewer"  # served, at least privilege
    ev = [e for e in events if e["event"] == "auth.system_role_claimed"]
    assert len(ev) == 1
    assert ev[0]["claimed_user_id"] == "mallory"
    assert ev[0]["path"] == "/api/x" and ev[0]["method"] == "POST"
    assert ev[0]["client"] == "203.0.113.9"
    line = [r for r in caplog.records if r.name == "ta_agent.security"]
    assert line and "auth.system_role_claimed" in line[0].getMessage()


def test_system_identity_claim_is_recorded_and_refused(events):
    with pytest.raises(HTTPException) as exc:
        _resolve_principal(_request(**{"X-User-Id": "system:scheduler", "X-User-Role": "admin"}))
    assert exc.value.status_code == 401
    ev = [e for e in events if e["event"] == "auth.system_identity_claimed"]
    assert len(ev) == 1 and ev[0]["claimed_user_id"] == "system:scheduler"
    assert ev[0]["claimed_role"] == "admin"


def test_security_events_reach_the_http_layer(events):
    import main

    r = TestClient(main.app).post("/api/emails/poll-replies", headers={"X-User-Role": "system"})
    assert r.status_code == 403
    ev = [e for e in events if e["event"] == "auth.system_role_claimed"]
    assert ev and ev[0]["path"] == "/api/emails/poll-replies"


def test_ordinary_requests_emit_no_security_events(events):
    _resolve_principal(_request(**{"X-User-Role": "recruiter", "X-User-Id": "u1"}))
    _resolve_principal(_request(**{"X-User-Role": "bogus"}))  # unknown role: downgraded, not an attack signal
    _resolve_principal(_request())
    assert events == []


def test_failing_sink_never_breaks_the_request(events):
    def bad_sink(_payload):
        raise RuntimeError("sink down")

    security_events.register_sink(bad_sink)
    p = _resolve_principal(_request(**{"X-User-Role": "system"}))
    assert p.role == "interviewer"
    assert any(e["event"] == "auth.system_role_claimed" for e in events)  # earlier sink still ran
