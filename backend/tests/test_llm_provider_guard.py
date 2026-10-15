"""Worker-side production check: the dev-only CLI LLM provider is refused
outside a dev-like APP_ENV, and the Celery worker runs that check at start-up."""

from __future__ import annotations

import pytest
from celery.signals import worker_init

from services.config import settings
from services.llm.guard import InsecureLLMConfig, assert_safe_llm_config


def _cfg(monkeypatch, *, provider, env):
    monkeypatch.setattr(settings, "llm_provider", provider)
    monkeypatch.setattr(settings, "app_env", env)


@pytest.mark.parametrize("env", ["prod", "production", "staging", "", None])
def test_cli_provider_is_refused_outside_dev(monkeypatch, env):
    _cfg(monkeypatch, provider="cli", env=env)
    with pytest.raises(InsecureLLMConfig) as exc:
        assert_safe_llm_config()
    msg = str(exc.value)
    for knob in ("LLM_PROVIDER", "APP_ENV", "ANTHROPIC_API_KEY"):
        assert knob in msg
    if not env:
        assert "unset" in msg


@pytest.mark.parametrize("env", ["dev", "test", "ci", " Local "])
def test_cli_provider_is_allowed_in_dev_like_envs(monkeypatch, env):
    _cfg(monkeypatch, provider="CLI", env=env)
    assert assert_safe_llm_config() == "cli"


@pytest.mark.parametrize("env", ["prod", "dev", ""])
def test_api_provider_is_fine_everywhere(monkeypatch, env):
    _cfg(monkeypatch, provider="api", env=env)
    assert assert_safe_llm_config() == "api"


def test_worker_init_runs_the_guard(monkeypatch):
    """The handler is connected to Celery's worker_init signal; Celery's `send`
    propagates handler exceptions, so a raise here aborts worker start-up."""
    import tasks.celery_app as mod  # registers the receiver

    _cfg(monkeypatch, provider="cli", env="prod")
    with pytest.raises(InsecureLLMConfig):
        mod._refuse_cli_llm_provider_outside_dev()
    with pytest.raises(InsecureLLMConfig):
        worker_init.send(sender=None)

    _cfg(monkeypatch, provider="cli", env="dev")
    worker_init.send(sender=None)  # no raise
