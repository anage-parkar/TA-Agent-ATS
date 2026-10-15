"""Celery application — the scheduled/background half of the TA Agent.

Until now, reply-polling and retention only ran when someone called the API
on demand (`POST /api/emails/poll-replies`, `POST
/api/admin/tenants/{id}/retention/run`). This wires the two jobs already
implemented in services/reply_poller.py and services/retention.py to a Celery
beat schedule so they run automatically, across every tenant, without a human
in the loop — appropriate here because both jobs only read/classify/expire
data per existing policy; they never take a reject/adverse decision. Each
tenant is bound under the non-human `system` principal (tasks/jobs.py), so the
repository's human-actor gate on rejection still holds for the scheduler.

Run from backend/ (same cwd as uvicorn, so config/env resolution matches):

    celery -A tasks.celery_app worker --loglevel=info --pool=solo   # Windows
    celery -A tasks.celery_app beat --loglevel=info                 # scheduler

(`./tasks.ps1 worker` / `make worker` and `./tasks.ps1 beat` / `make beat`
run the two lines above. Run them in separate windows during development, or
add both to your process manager for a real deployment.)

Requires Redis (`./tasks.ps1 up`) as both broker and result backend.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_init

from services.config import settings

celery_app = Celery(
    "ta_agent",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "poll-email-replies": {
            "task": "tasks.jobs.poll_replies_all_tenants",
            "schedule": settings.celery_poll_replies_interval_seconds,
        },
        "enforce-retention": {
            "task": "tasks.jobs.enforce_retention_all_tenants",
            "schedule": crontab(hour=settings.celery_retention_hour_utc, minute=0),
        },
    },
)

@worker_init.connect
def _refuse_cli_llm_provider_outside_dev(**_kwargs) -> None:
    """Worker-side production check: the Claude Code CLI LLM provider is for
    local development only. Celery's `send` propagates handler exceptions, so
    raising here aborts worker start-up with the guard's message."""
    from services.llm.guard import assert_safe_llm_config

    assert_safe_llm_config()


# Import after celery_app exists so the @celery_app.task decorators in
# jobs.py register against this instance (tasks/jobs.py imports celery_app
# back from this module — see its docstring).
from tasks import jobs  # noqa: E402,F401
