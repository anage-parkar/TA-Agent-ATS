"""Tenant-scoped LangGraph checkpointers (Phase 2 / PR 1).

Two savers, one discipline:

* ``TenantPostgresSaver`` — the package's ``PostgresSaver`` with its only
  connection seam (``_cursor``) redirected through
  ``db.supabase_client.tenant_connection()``, so every read and write runs on
  a connection whose ``app.tenant_id`` GUC is set and Postgres RLS (migration
  0011) scopes it. A guessed ``thread_id`` from another tenant returns nothing;
  an unbound connection sees zero rows. ``setup()`` is refused: the schema is
  owned by migrations and the app role has no CREATE privilege.
* ``TenantMemorySaver`` — the package's ``InMemorySaver`` with the same
  ownership rule the in-memory repository applies: a thread belongs to the
  tenant that first wrote it; other tenants read nothing, delete nothing and
  are refused writes.

Which one runs is **explicit configuration** (``CHECKPOINTER_BACKEND``), never
a fallback on database availability: outside a dev-like ``APP_ENV`` the memory
saver is refused, and a configured-but-unreachable Postgres fails startup.
"""

from __future__ import annotations

import base64
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata, CheckpointTuple
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row

from services.auth import is_dev_like
from services.config import settings

BACKENDS = ("postgres", "memory")


def _serde() -> JsonPlusSerializer:
    """The checkpoint serializer, with our state types registered explicitly.
    LangGraph refuses (soon: blocks) deserialising unregistered types; graph
    state is exactly the types in orchestrator.state and nothing else."""
    from orchestrator import state as state_mod

    allowed = [(state_mod.__name__, name) for name in state_mod.__all__]
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)


class CheckpointerConfigError(RuntimeError):
    """The checkpointer configuration is unsafe or unusable. Raised at startup."""


class CheckpointerSchemaManagedByMigrations(RuntimeError):
    """``setup()`` was called. The checkpoint schema is owned by
    supabase/migrations/0011_checkpointer.sql and must never be created by the
    package (nor could it be: the app role has no CREATE privilege)."""


# ── Postgres ────────────────────────────────────────────────────────────
class TenantPostgresSaver(PostgresSaver):
    """PostgresSaver whose every operation borrows a tenant-bound connection."""

    def __init__(self, *, serde=None) -> None:
        # No connection is held; one is borrowed per operation from the
        # repository's pool with the tenant GUC already set.
        super().__init__(conn=None, serde=serde)  # type: ignore[arg-type]

    @contextmanager
    def _cursor(self, *, pipeline: bool = False) -> Iterator[Any]:
        from db.supabase_client import tenant_connection  # lazy: avoids import cycle

        with self.lock, tenant_connection() as conn:
            if pipeline and self.supports_pipeline:
                with conn.pipeline(), conn.cursor(binary=True, row_factory=dict_row) as cur:
                    yield cur
            else:
                with conn.cursor(binary=True, row_factory=dict_row) as cur:
                    yield cur

    def setup(self) -> None:  # noqa: D401 — deliberately refused
        raise CheckpointerSchemaManagedByMigrations(
            "The LangGraph checkpoint schema is managed by "
            "supabase/migrations/0011_checkpointer.sql; never call setup()."
        )


# ── In-memory (dev / tests) ─────────────────────────────────────────────
def _b64(data: bytes | bytearray | memoryview | None) -> str | None:
    return None if data is None else base64.b64encode(bytes(data)).decode("ascii")


class TenantMemorySaver(InMemorySaver):
    """InMemorySaver with tenant ownership: mirrors what RLS does in Postgres."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._owner: dict[str, str] = {}   # thread_id -> tenant_id

    @staticmethod
    def _tenant() -> str:
        from services.tenant_context import get_tenant_id

        return get_tenant_id()

    def _owned(self, thread_id: str) -> bool:
        owner = self._owner.get(thread_id)
        return owner is None or owner == self._tenant()

    def _claim(self, thread_id: str) -> None:
        if not self._owned(thread_id):
            raise PermissionError(f"thread {thread_id} belongs to another tenant")
        self._owner.setdefault(thread_id, self._tenant())

    # reads ------------------------------------------------------------
    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        if not self._owned(config["configurable"]["thread_id"]):
            return None
        return super().get_tuple(config)

    def list(self, config: RunnableConfig | None, **kwargs: Any) -> Iterator[CheckpointTuple]:
        if config is not None and not self._owned(config["configurable"]["thread_id"]):
            return iter(())
        return (t for t in super().list(config, **kwargs)
                if self._owned(t.config["configurable"]["thread_id"]))

    # writes -----------------------------------------------------------
    def put(self, config: RunnableConfig, checkpoint: Checkpoint,
            metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        self._claim(config["configurable"]["thread_id"])
        return super().put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config: RunnableConfig, writes, task_id: str, task_path: str = "") -> None:
        self._claim(config["configurable"]["thread_id"])
        super().put_writes(config, writes, task_id, task_path)

    def delete_thread(self, thread_id: str) -> None:
        if not self._owned(thread_id):
            return  # like an RLS-scoped DELETE: zero rows affected
        super().delete_thread(thread_id)
        self._owner.pop(thread_id, None)

    # lifecycle (export / erasure parity with the Postgres tables) -----
    def export_thread(self, thread_id: str) -> dict[str, list[dict]]:
        """The thread's rows in the shape of the three Postgres tables."""
        out: dict[str, list[dict]] = {"checkpoints": [], "checkpoint_blobs": [], "checkpoint_writes": []}
        if not self._owned(thread_id):
            return out
        for ns, by_id in self.storage.get(thread_id, {}).items():
            for cid, (cp, md, parent) in by_id.items():
                out["checkpoints"].append({
                    "thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": cid,
                    "parent_checkpoint_id": parent, "type": cp[0],
                    "checkpoint": _b64(cp[1]), "metadata": _b64(md[1]),
                })
        for (tid, ns, channel, version), (typ, blob) in self.blobs.items():
            if tid == thread_id:
                out["checkpoint_blobs"].append({
                    "thread_id": tid, "checkpoint_ns": ns, "channel": channel,
                    "version": version, "type": typ, "blob": _b64(blob),
                })
        for (tid, ns, cid), inner in self.writes.items():
            if tid == thread_id:
                for (task_id, idx), (_, channel, value, task_path) in inner.items():
                    out["checkpoint_writes"].append({
                        "thread_id": tid, "checkpoint_ns": ns, "checkpoint_id": cid,
                        "task_id": task_id, "idx": idx, "channel": channel,
                        "type": value[0], "blob": _b64(value[1]), "task_path": task_path,
                    })
        return out


# ── Selection: explicit config, no availability fallback ─────────────────
def resolve_backend(cfg=None) -> str:
    """``CHECKPOINTER_BACKEND`` if set; otherwise memory in dev-like envs and
    postgres everywhere else. Never derived from whether the DB is reachable."""
    cfg = settings if cfg is None else cfg
    explicit = (getattr(cfg, "checkpointer_backend", "") or "").strip().lower()
    if explicit:
        return explicit
    return "memory" if is_dev_like(cfg.app_env) else "postgres"


def assert_safe_checkpointer_config(cfg=None) -> str:
    """Refuse to start with an unsafe or unusable checkpointer. Returns the backend."""
    from db.supabase_client import db_available  # lazy: avoids import cycle

    cfg = settings if cfg is None else cfg
    backend = resolve_backend(cfg)
    if backend not in BACKENDS:
        raise CheckpointerConfigError(
            f"CHECKPOINTER_BACKEND={backend!r} is not one of {BACKENDS}."
        )
    env = (cfg.app_env or "").strip().lower() or "<unset: treated as production>"
    if backend == "memory" and not is_dev_like(cfg.app_env):
        raise CheckpointerConfigError(
            f"Refusing to start: CHECKPOINTER_BACKEND=memory in APP_ENV={env}. The "
            "in-memory checkpointer is for dev/test only; set CHECKPOINTER_BACKEND=postgres."
        )
    if backend == "postgres" and not db_available():
        raise CheckpointerConfigError(
            "Refusing to start: CHECKPOINTER_BACKEND=postgres but Postgres is unreachable. "
            "There is no fallback to the memory saver; fix DATABASE_URL (or, for local "
            "dev only, set CHECKPOINTER_BACKEND=memory)."
        )
    return backend


_instance: TenantPostgresSaver | TenantMemorySaver | None = None
_instance_lock = threading.Lock()


def get_checkpointer() -> TenantPostgresSaver | TenantMemorySaver:
    """The process-wide checkpointer for the configured backend (built lazily)."""
    global _instance
    with _instance_lock:
        if _instance is None:
            backend = resolve_backend()
            if backend == "postgres":
                _instance = TenantPostgresSaver(serde=_serde())
            elif backend == "memory":
                _instance = TenantMemorySaver(serde=_serde())
            else:
                raise CheckpointerConfigError(f"unknown CHECKPOINTER_BACKEND {backend!r}")
        return _instance


def memory_saver_if_active() -> TenantMemorySaver | None:
    """The in-memory saver when that is the configured backend, else None.

    Used by the repository's in-memory lifecycle paths (export / erasure) so
    they never touch Postgres through the saver.
    """
    if resolve_backend() != "memory":
        return None
    saver = get_checkpointer()
    return saver if isinstance(saver, TenantMemorySaver) else None


def reset_checkpointer() -> None:
    """Drop the cached instance (tests, or after changing the backend setting)."""
    global _instance
    with _instance_lock:
        _instance = None
