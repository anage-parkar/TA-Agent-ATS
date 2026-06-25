"""Usage attribution, cost estimation, and per-tenant rate limiting.

Every gateway call is recorded here with its tenant id, prompt version, model
version, and token usage (structured log line + pluggable sinks). Workstream F
attaches a DB sink via `register_sink` for per-tenant cost reporting; until then
the structured log is the record.

The RateLimiter is process-local and in-memory — correct for a single worker
and good enough to prove the cap works. Workstream A/F can swap a Redis-backed
limiter behind the same `.check(tenant_id)` interface.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Callable

from services.llm.base import LLMRateLimitError, LLMResult, TokenUsage

logger = logging.getLogger("ta_agent.llm.usage")

# USD per 1M tokens (input, output). Keep in sync with the model aliases in
# base.API_MODEL_ALIASES. Unknown models cost 0 (cost is best-effort).
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost_usd(model_version: str, usage: TokenUsage) -> float:
    inp, out = _PRICING.get(model_version, (0.0, 0.0))
    return (usage.input_tokens / 1_000_000) * inp + (usage.output_tokens / 1_000_000) * out


# Sinks let later workstreams persist usage without this module importing the
# DB layer. Each sink receives the result and the estimated cost.
_SINKS: list[Callable[[LLMResult, float], None]] = []


def register_sink(sink: Callable[[LLMResult, float], None]) -> None:
    _SINKS.append(sink)


def clear_sinks() -> None:
    _SINKS.clear()


def record_usage(result: LLMResult) -> float:
    """Log + fan out one call's usage. Returns the estimated cost (USD)."""
    cost = estimate_cost_usd(result.model_version, result.usage)
    logger.info(
        "llm_call tenant=%s provider=%s prompt=%s@v%d model=%s in=%d out=%d "
        "cost_usd=%.6f latency_ms=%d input_hash=%s",
        result.tenant_id,
        result.provider,
        result.prompt_name,
        result.prompt_version,
        result.model_version,
        result.usage.input_tokens,
        result.usage.output_tokens,
        cost,
        result.latency_ms,
        result.input_hash[:12],
    )
    for sink in _SINKS:
        try:
            sink(result, cost)
        except Exception:  # noqa: BLE001 — a bad sink must not fail the call
            logger.exception("usage sink raised; continuing")
    return cost


class RateLimiter:
    """Sliding-window request limiter, keyed per tenant.

    `per_minute <= 0` disables limiting. Thread-safe (scoring fans out across a
    ThreadPoolExecutor), so the window deque is guarded by a lock.
    """

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, tenant_id: str) -> None:
        if self.per_minute <= 0:
            return
        now = time.monotonic()
        window = 60.0
        with self._lock:
            dq = self._events[tenant_id]
            while dq and now - dq[0] > window:
                dq.popleft()
            if len(dq) >= self.per_minute:
                raise LLMRateLimitError(
                    f"tenant {tenant_id!r} exceeded {self.per_minute} LLM calls/min"
                )
            dq.append(now)
