"""LLM gateway package — the single choke point for all model calls.

Public API:
    get_gateway()        -> the configured LLMGateway singleton
    LLMGateway           -> the gateway class
    LLMError             -> raised on any model failure
    LLMRateLimitError    -> raised when a tenant exceeds its rate cap
    prompts              -> the versioned prompt registry

Provider selection is config-only (LLM_PROVIDER=api|cli). See gateway.py.
"""

from __future__ import annotations

from services.llm import prompts
from services.llm.base import (
    API_MODEL_ALIASES,
    LLMError,
    LLMRateLimitError,
    LLMResult,
    Provider,
    ProviderResponse,
    TokenUsage,
)
from services.llm.gateway import (
    DEFAULT_TENANT,
    LLMGateway,
    get_gateway,
    reset_gateway,
    set_gateway,
)

__all__ = [
    "API_MODEL_ALIASES",
    "DEFAULT_TENANT",
    "LLMError",
    "LLMRateLimitError",
    "LLMGateway",
    "LLMResult",
    "Provider",
    "ProviderResponse",
    "TokenUsage",
    "get_gateway",
    "prompts",
    "reset_gateway",
    "set_gateway",
]
