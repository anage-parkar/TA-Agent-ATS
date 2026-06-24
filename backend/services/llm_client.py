"""Backwards-compatible shim over the LLM gateway.

The single LLM choke point now lives in `services.llm` (the LLMGateway and its
providers). This module is retained only so existing imports keep working:

    from services.llm_client import LLMError, call_claude, call_claude_json
    from services.llm_client import CLAUDE_BIN, cli_available

New code should call the gateway directly with a registered prompt:

    from services.llm import get_gateway
    data = get_gateway().complete_json(prompt="scoring.ats", user=...)

`call_claude` / `call_claude_json` route through the gateway (and therefore the
configured provider — API-key auth in production), so no code path here invokes
`claude -p` directly.
"""

from __future__ import annotations

from services.llm import LLMError, get_gateway
from services.llm.providers.cli import CLAUDE_BIN, cli_available

__all__ = ["LLMError", "call_claude", "call_claude_json", "CLAUDE_BIN", "cli_available"]


def call_claude(
    system: str, user: str, max_tokens: int = 1000, model: str | None = None
) -> str:
    """Deprecated. Prefer get_gateway().complete(prompt=..., user=...)."""
    return get_gateway().complete(
        system=system, user=user, max_tokens=max_tokens, model=model
    )


def call_claude_json(
    system: str, user: str, max_tokens: int = 1000, model: str | None = None
) -> dict:
    """Deprecated. Prefer get_gateway().complete_json(prompt=..., user=...)."""
    return get_gateway().complete_json(
        system=system, user=user, max_tokens=max_tokens, model=model
    )
