"""Shared test fixtures for the LLM gateway workstream."""

from __future__ import annotations

import pytest

from services.llm import reset_gateway, set_gateway
from services.llm import usage as usage_mod
from services.llm.base import Provider, ProviderResponse, TokenUsage
from services.llm.gateway import LLMGateway
from services.llm.usage import RateLimiter


class RecordingProvider(Provider):
    """A fake provider that returns canned text and records every call.

    Stands in for the real Anthropic API provider in tests — the gateway,
    stamping, usage, and rate-limiting logic are identical regardless of which
    provider sits underneath, so this exercises the full call path without
    network access.
    """

    name = "anthropic_api"  # masquerade as the API provider for assertions

    def __init__(self, text: str = '{"ok": true}', model_version: str = "claude-sonnet-4-6"):
        self.text = text
        self.model_version = model_version
        self.calls: list[dict] = []
        self._available = True

    def available(self) -> bool:
        return self._available

    def complete(self, system, user, *, max_tokens, model) -> ProviderResponse:
        self.calls.append(
            {"system": system, "user": user, "max_tokens": max_tokens, "model": model}
        )
        return ProviderResponse(
            text=self.text,
            model_version=self.model_version,
            usage=TokenUsage(input_tokens=100, output_tokens=20),
        )


@pytest.fixture
def make_gateway():
    """Factory: build a gateway around a provider with an optional rate cap."""

    def _factory(provider: Provider, *, per_minute: int = 0, default_model: str = "sonnet"):
        gw = LLMGateway(
            provider,
            default_model=default_model,
            rate_limiter=RateLimiter(per_minute),
        )
        set_gateway(gw)
        return gw

    yield _factory
    reset_gateway()


@pytest.fixture(autouse=True)
def _isolate_usage_sinks():
    """Keep usage sinks empty per test, and capture recorded results."""
    usage_mod.clear_sinks()
    captured: list = []
    usage_mod.register_sink(lambda result, cost: captured.append((result, cost)))
    yield captured
    usage_mod.clear_sinks()
