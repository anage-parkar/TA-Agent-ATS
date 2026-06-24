"""Anthropic API provider — the production inference backend (API-key auth).

Uses the official `anthropic` SDK with an Anthropic Console API key
(`ANTHROPIC_API_KEY`). This is the only provider permitted in production
(Engineering Rule 1). The SDK handles retries with exponential backoff (429 /
5xx / connection errors) and request timeouts; we surface failures as LLMError.
"""

from __future__ import annotations

import logging

from services.config import settings
from services.llm.base import (
    API_MODEL_ALIASES,
    LLMError,
    Provider,
    ProviderResponse,
    TokenUsage,
)

logger = logging.getLogger("ta_agent.llm.anthropic")


class AnthropicAPIProvider(Provider):
    name = "anthropic_api"

    def __init__(self) -> None:
        self._client = None  # lazily constructed so import never needs a key

    def available(self) -> bool:
        return bool(settings.anthropic_api_key)

    def _get_client(self):
        if self._client is None:
            if not settings.anthropic_api_key:
                raise LLMError(
                    "ANTHROPIC_API_KEY is not set. Provide an Anthropic Console "
                    "API key, or use LLM_PROVIDER=cli for local development."
                )
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dep is in requirements
                raise LLMError(
                    "anthropic SDK not installed; add `anthropic` to requirements.txt"
                ) from exc
            self._client = anthropic.Anthropic(
                api_key=settings.anthropic_api_key,
                max_retries=settings.llm_max_retries,
                timeout=float(settings.llm_timeout),
            )
        return self._client

    @staticmethod
    def _resolve_model(model: str) -> str:
        return API_MODEL_ALIASES.get(model, model)

    def complete(
        self, system: str, user: str, *, max_tokens: int, model: str
    ) -> ProviderResponse:
        client = self._get_client()
        import anthropic  # safe: _get_client validated it is installed

        model_id = self._resolve_model(model)
        try:
            resp = client.messages.create(
                model=model_id,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIStatusError as exc:
            logger.error("Anthropic API error %s: %s", exc.status_code, exc.message)
            raise LLMError(
                f"Anthropic API error {exc.status_code}: {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Anthropic API connection error: {exc}") from exc

        if resp.stop_reason == "refusal":
            raise LLMError("Anthropic API declined the request (stop_reason=refusal).")

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            raise LLMError("Anthropic API returned no text content.")

        u = resp.usage
        usage = TokenUsage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        return ProviderResponse(
            text=text, model_version=resp.model or model_id, usage=usage
        )
