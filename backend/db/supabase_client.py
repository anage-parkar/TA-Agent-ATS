"""Supabase / Postgres connection.

For local dev we talk to Postgres directly via psycopg using DATABASE_URL.
A lightweight connection pool is created lazily on first use. If the database
is unreachable (e.g. Docker not yet running), `db_available()` returns False
and the repository layer falls back to an in-memory store so the first
vertical slice still runs end-to-end.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from services.config import settings

logger = logging.getLogger("ta_agent.db")

_pool = None
_checked = False
_available = False


def _init_pool():
    global _pool, _checked, _available
    if _checked:
        return
    _checked = True
    try:
        import psycopg
        from psycopg_pool import ConnectionPool

        # Fast availability pre-check so we don't spin up the pool's background
        # reconnect threads when the DB is simply not running yet.
        with psycopg.connect(settings.database_url, connect_timeout=2) as probe:
            probe.execute("select 1")

        # check= validates a connection before handing it out (reconnecting if
        # the Supabase pooler idle-closed it); max_idle keeps us under that
        # cutoff. Without this, a long request can grab a dead connection and
        # fail with "server closed the connection unexpectedly".
        pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=5,
            open=True,
            check=ConnectionPool.check_connection,
            max_idle=120,
            max_lifetime=600,
        )
        _pool = pool
        _available = True
        logger.info("Connected to Postgres at %s", settings.database_url)
    except Exception as exc:  # noqa: BLE001 — any failure means "use fallback"
        logger.warning(
            "Postgres unavailable (%s). Falling back to in-memory store. "
            "Start Docker + run migrations for real persistence.",
            exc,
        )
        _available = False


def db_available() -> bool:
    _init_pool()
    return _available


def get_pool():
    _init_pool()
    if not _available:
        raise RuntimeError("Postgres is not available.")
    return _pool


@contextmanager
def tenant_connection():
    """Yield a pooled connection with `app.tenant_id` bound for RLS.

    The GUC is set with `set_config(..., is_local => true)` so it is scoped to
    the connection's current transaction and is automatically discarded when the
    pool commits/rolls back and returns the connection — no leakage between
    tenants sharing the pool. RLS policies read `current_setting('app.tenant_id')`,
    so reads are tenant-filtered even when a query omits a WHERE clause, and the
    column default fills tenant_id on inserts from the same GUC.
    """
    # Imported here to avoid a circular import at module load.
    from services.tenant_context import get_tenant_id

    tenant_id = get_tenant_id()
    with get_pool().connection() as conn:
        conn.execute("select set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        yield conn


def close_pool() -> None:
    """Close the connection pool cleanly (call on app shutdown)."""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:  # noqa: BLE001
            pass
        _pool = None
