"""Core types shared across the LLM gateway, providers, and call sites.

This module has no internal dependencies (only stdlib) so providers, the
gateway, usage accounting, and the prompt registry can all import from it
without creating cycles.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class LLMError(RuntimeError):
    """Raised when a model call fails or returns unusable output."""


class LLMRateLimitError(LLMError):
    """Raised when a tenant exceeds its configured request rate."""


# Alias → current production Anthropic model id. The codebase has always
# addressed models by alias ('sonnet' workhorse, 'haiku' for cheap email
# drafting); we keep those aliases and map them to the current model ids here.
# A full model id passed through is used verbatim.
API_MODEL_ALIASES: dict[str, str] = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
}


@dataclass(frozen=True)
class TokenUsage:
    """Token accounting for a single model call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ProviderResponse:
    """What a Provider returns from a single completion."""

    text: str
    model_version: str           # the model id the provider actually served
    usage: TokenUsage


@dataclass
class LLMResult:
    """Full, stamped record of one gateway call — the audit unit.

    Everything Workstream B needs to populate an `ai_decisions` row (model
    version, prompt version, input hash, token usage) is captured here.
    """

    text: str
    provider: str
    model_requested: str
    model_version: str
    prompt_name: str
    prompt_version: int
    prompt_hash: str
    input_hash: str
    tenant_id: str
    usage: TokenUsage
    latency_ms: int
    data: Optional[dict] = None   # populated by complete_json()


def input_hash(system: str, user: str) -> str:
    """Stable hash of the exact (system, user) inputs to a call.

    Stored on every AIDecision so a score can be tied back to the precise
    bytes that produced it (and to detect re-scoring of identical input).
    """
    h = hashlib.sha256()
    h.update(system.encode("utf-8"))
    h.update(b"\x00")
    h.update(user.encode("utf-8"))
    return h.hexdigest()


class Provider(ABC):
    """A backend that can turn (system, user) into assistant text.

    Implementations: AnthropicAPIProvider (production, API-key auth) and
    CLIProvider (local dev only, Claude Code CLI). The gateway is the only
    caller; it adds prompt resolution, stamping, usage, and rate limiting.
    """

    name: str = "base"

    @abstractmethod
    def complete(
        self, system: str, user: str, *, max_tokens: int, model: str
    ) -> ProviderResponse:
        """Run one completion. Raise LLMError on any failure."""

    def available(self) -> bool:
        """True if the provider is configured and ready to serve calls."""
        return True
