"""Production guard for the LLM provider (Engineering Rule 1).

The Claude Code CLI provider (``LLM_PROVIDER=cli``) runs on consumer OAuth
credentials and is permitted for local development only. Any process that can
reach the gateway must refuse to start with it outside a dev-like ``APP_ENV``.
Fails closed: an unset ``APP_ENV`` counts as production.

Wired into the Celery worker (``tasks/celery_app.py``, ``worker_init``), where
reply classification runs unattended.
"""

from __future__ import annotations

from services.auth import is_dev_like
from services.config import settings


class InsecureLLMConfig(RuntimeError):
    """The dev-only CLI LLM provider is configured outside a dev-like environment."""


def assert_safe_llm_config(cfg=None) -> str:
    """Refuse the CLI provider outside dev. Returns the provider name."""
    cfg = settings if cfg is None else cfg
    provider = (cfg.llm_provider or "").strip().lower()
    if provider == "cli" and not is_dev_like(cfg.app_env):
        env = (cfg.app_env or "").strip().lower() or "<unset: treated as production>"
        raise InsecureLLMConfig(
            f"Refusing to start: LLM_PROVIDER=cli in APP_ENV={env}. The Claude Code CLI "
            "provider runs on consumer OAuth credentials and is for local development "
            "only. Set LLM_PROVIDER=api with ANTHROPIC_API_KEY, or APP_ENV=dev for local work."
        )
    return provider
