"""Event backbone (Workstream B).

`emit_event` is the one place domain events are raised. Each event is persisted
to the append-only `activity_events` audit log (via the repository) AND fanned
out to in-process subscribers — the seam that automations, analytics, and
notifications hook into. Stage transitions, AI decisions, and human decisions
all flow through here so the audit log and downstream consumers stay in sync.

(Low-level state changes in the repository — application create/update — write
their own audit rows directly; emit_event is for richer domain events raised by
routers/agents, e.g. "candidate.scored".)
"""

from __future__ import annotations

import logging
from typing import Callable

from db import repository

logger = logging.getLogger("ta_agent.events")

Subscriber = Callable[[dict], None]
_subscribers: list[Subscriber] = []


def subscribe(fn: Subscriber) -> None:
    _subscribers.append(fn)


def clear_subscribers() -> None:
    _subscribers.clear()


def emit_event(
    action: str,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    actor_type: str = "human",
    actor_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    event = repository.create_activity_event(
        {
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "before": before,
            "after": after,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "metadata": metadata,
        }
    )
    for sub in _subscribers:
        try:
            sub(event)
        except Exception:  # noqa: BLE001 — a bad subscriber must not break the flow
            logger.exception("event subscriber failed for %s", action)
    return event


# Default subscriber: structured log line. Automations/analytics add their own.
subscribe(
    lambda e: logger.info(
        "event=%s entity=%s/%s actor=%s/%s",
        e.get("action"), e.get("entity_type"), e.get("entity_id"),
        e.get("actor_type"), e.get("actor_id"),
    )
)
