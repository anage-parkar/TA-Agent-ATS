"""Worker-side production check: the dev-only CLI LLM provider is refused
outside a dev-like APP_ENV, and the Celery app module runs that check at
import — the only wiring that actually aborts a worker (Celery's Signal.send
swallows handler exceptions, so a worker_init hook would be silently useless)."""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

import pytest

from services.config import settings
from services.llm.guard import InsecureLLMConfig, assert_safe_llm_config

BACKEND = pathlib.Path(__file__).resolve().parents[1]


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


def test_celery_app_module_calls_the_guard_at_top_level():
    """Structural: the call must be a module-level statement, not inside a
    function or signal handler, so merely loading the app refuses to continue."""
    tree = ast.parse((BACKEND / "tasks" / "celery_app.py").read_text(encoding="utf-8"))
    top_level_calls = [
        node.value.func.id
        for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    ]
    assert "assert_safe_llm_config" in top_level_calls


def _import_celery_app(**env_overrides) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        [sys.executable, "-c", "import tasks.celery_app"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120,
    )


def test_loading_the_celery_app_refuses_cli_outside_dev():
    bad = _import_celery_app(APP_ENV="prod", LLM_PROVIDER="cli")
    assert bad.returncode != 0
    assert "InsecureLLMConfig" in bad.stderr
    assert "LLM_PROVIDER=cli in APP_ENV=prod" in bad.stderr


def test_loading_the_celery_app_is_fine_in_dev_and_with_the_api_provider():
    assert _import_celery_app(APP_ENV="dev", LLM_PROVIDER="cli").returncode == 0
    assert _import_celery_app(APP_ENV="prod", LLM_PROVIDER="api").returncode == 0
