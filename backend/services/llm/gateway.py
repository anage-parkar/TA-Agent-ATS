"""LLMGateway — the single choke point for every model call (Engineering Rule 1).

All inference flows through one `LLMGateway` instance, selected by config:

    LLM_PROVIDER=api   → AnthropicAPIProvider (API-key auth) — production default
    LLM_PROVIDER=cli   → CLIProvider (Claude Code CLI) — local development only

Switching providers is config-only. Every call is stamped with prompt
name+version, model version, tenant id, input hash, and token usage, and
recorded via `usage.record_usage`. Rate limiting is enforced per tenant before
the provider is invoked.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

from services.config import settings
from services.llm import prompts
from services.llm.base import (
    LLMError,
    LLMResult,
    Provider,
    input_hash,
)
from services.llm.base import input_hash as _input_hash  # noqa: F401 (re-export clarity)
from services.llm.prompts import PromptTemplate
from services.llm.usage import RateLimiter, record_usage

logger = logging.getLogger("ta_agent.llm.gateway")

DEFAULT_TENANT = "default"

T = TypeVar("T")

# Appended to the user prompt for JSON calls. Curbs the model's tendency to
# fence the JSON or add a prose/table summary after it.
_JSON_DIRECTIVE = (
    "\n\nIMPORTANT: Respond with ONLY the raw JSON object and nothing else — "
    "no markdown code fences, no commentary, no summary tables, no trailing commas."
)


def _extract_json_object(text: str) -> str | None:
    """Return the first balanced top-level {...} object found in `text`.

    Survives surrounding markdown fences and any prose/tables appended after
    the JSON. String contents (incl. escaped quotes) are respected.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _parse_json(raw: str) -> dict:
    """Recover a JSON object from model output. Raises LLMError if none found."""
    extracted = _extract_json_object(raw)
    for candidate in (raw, extracted):
        if not candidate:
            continue
        for attempt in (candidate, _remove_trailing_commas(candidate)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    logger.error("LLM returned non-JSON output: %s", raw[:500])
    raise LLMError("Expected JSON from LLM but could not recover a JSON object.")


class LLMGateway:
    def __init__(
        self,
        provider: Provider,
        *,
        default_model: str,
        rate_limiter: RateLimiter,
    ) -> None:
        self._provider = provider
        self._default_model = default_model
        self._rate_limiter = rate_limiter

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def available(self) -> bool:
        return self._provider.available()

    def _resolve(
        self,
        *,
        prompt: str | None,
        system: str | None,
        model: str | None,
        max_tokens: int | None,
    ) -> tuple[str, str, int, str, str, int]:
        """Resolve (system, prompt_name, prompt_version, prompt_hash, model, max_tokens)."""
        if prompt is not None:
            tmpl: PromptTemplate = prompts.get(prompt)
            return (
                tmpl.system,
                tmpl.name,
                tmpl.version,
                tmpl.hash,
                model or tmpl.default_model or self._default_model,
                max_tokens if max_tokens is not None else tmpl.default_max_tokens,
            )
        if system is None:
            raise LLMError("LLM call requires either prompt= (registry) or system= (raw).")
        # Ad-hoc/raw system text — discouraged but supported for back-compat.
        import hashlib

        h = hashlib.sha256(system.encode("utf-8")).hexdigest()
        return (
            system,
            "adhoc",
            0,
            h,
            model or self._default_model,
            max_tokens if max_tokens is not None else 1000,
        )

    def complete(
        self,
        *,
        user: str,
        prompt: str | None = None,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        tenant_id: str = DEFAULT_TENANT,
        with_meta: bool = False,
    ):
        """Run one completion. Returns the assistant text (or LLMResult if with_meta)."""
        sys_text, p_name, p_ver, p_hash, model_final, max_tok = self._resolve(
            prompt=prompt, system=system, model=model, max_tokens=max_tokens
        )
        self._rate_limiter.check(tenant_id)

        start = time.monotonic()
        try:
            pr = self._provider.complete(
                sys_text, user, max_tokens=max_tok, model=model_final
            )
        except LLMError:
            logger.exception(
                "LLM call failed tenant=%s prompt=%s@v%s model=%s",
                tenant_id,
                p_name,
                p_ver,
                model_final,
            )
            raise
        latency_ms = int((time.monotonic() - start) * 1000)

        result = LLMResult(
            text=pr.text,
            provider=self._provider.name,
            model_requested=model_final,
            model_version=pr.model_version,
            prompt_name=p_name,
            prompt_version=p_ver,
            prompt_hash=p_hash,
            input_hash=input_hash(sys_text, user),
            tenant_id=tenant_id,
            usage=pr.usage,
            latency_ms=latency_ms,
        )
        record_usage(result)
        return result if with_meta else result.text

    def complete_json(
        self,
        *,
        user: str,
        prompt: str | None = None,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        tenant_id: str = DEFAULT_TENANT,
        with_meta: bool = False,
    ):
        """Run one completion and parse JSON. Returns the dict (or LLMResult if with_meta)."""
        result: LLMResult = self.complete(
            user=f"{user}{_JSON_DIRECTIVE}",
            prompt=prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            tenant_id=tenant_id,
            with_meta=True,
        )
        result.data = _parse_json(result.text)
        return result if with_meta else result.data

    def parallel(
        self, calls: list[Callable[[], T]], *, max_workers: int | None = None
    ) -> list[T]:
        """Run independent gateway calls concurrently (e.g. scoring N candidates).

        Each item is a zero-arg callable (typically a lambda wrapping a
        complete_json call). Concurrency defaults to settings.llm_max_concurrency.
        """
        if not calls:
            return []
        workers = max_workers or settings.llm_max_concurrency
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(calls)))) as pool:
            return list(pool.map(lambda fn: fn(), calls))


# ---------------------------------------------------------------------------
# Singleton wiring
# ---------------------------------------------------------------------------

_gateway: LLMGateway | None = None


def _make_provider(name: str) -> Provider:
    if name == "cli":
        from services.llm.providers.cli import CLIProvider

        return CLIProvider()
    if name == "api":
        from services.llm.providers.anthropic_api import AnthropicAPIProvider

        return AnthropicAPIProvider()
    raise LLMError(f"unknown LLM_PROVIDER {name!r}; use 'api' or 'cli'")


def _build_gateway() -> LLMGateway:
    provider = _make_provider(settings.llm_provider)
    return LLMGateway(
        provider,
        default_model=settings.llm_model,
        rate_limiter=RateLimiter(settings.llm_rate_limit_per_min),
    )


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = _build_gateway()
    return _gateway


def set_gateway(gateway: LLMGateway | None) -> None:
    """Inject a gateway (tests). Pass None to force a rebuild on next get."""
    global _gateway
    _gateway = gateway


def reset_gateway() -> None:
    """Drop the cached gateway so the next get_gateway() rebuilds from config."""
    global _gateway
    _gateway = None
