"""Security-relevant events: auth anomalies and insecure-config overrides.

Every event is one structured line on the ``ta_agent.security`` logger (a JSON
payload after the ``security_event`` marker, so it is trivial to ship to a
SIEM and alert on), plus pluggable sinks — the same pattern as
``services.llm.usage`` — so a webhook/alerting sink can be registered without
touching call sites.

Events are deliberately NOT written to the tenant-scoped ``activity_events``
audit table: they are raised before any principal is bound, and the tenant
header on an offending request may itself be spoofed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("ta_agent.security")

Sink = Callable[[dict[str, Any]], None]
_sinks: list[Sink] = []

_LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "critical": logging.CRITICAL}


def register_sink(sink: Sink) -> None:
    """Fan every event out to ``sink(payload)`` as well as the log."""
    _sinks.append(sink)


def clear_sinks() -> None:
    _sinks.clear()


def record(event: str, *, severity: str = "warning", **fields: Any) -> dict[str, Any]:
    """Record a security event. Never raises: a failing sink is logged and skipped."""
    payload: dict[str, Any] = {
        "event": event,
        "severity": severity,
        "at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    logger.log(
        _LEVELS.get(severity, logging.WARNING),
        "security_event %s",
        json.dumps(payload, default=str, sort_keys=True),
    )
    for sink in list(_sinks):
        try:
            sink(payload)
        except Exception:  # noqa: BLE001 — a sink must never break the request path
            logger.exception("security event sink failed for %s", event)
    return payload
