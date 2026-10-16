"""Graph-version migrations (Phase 2 / PR 2 — scaffold).

`MIGRATIONS[v]` migrates a thread's state *from* graph version `v` to `v + 1`
(a callable taking and returning the state values dict), or is the
`NO_MIGRATION` sentinel, which strands threads at that version into human
review instead of guessing. A CI test requires an entry for every version
below the current one. Node-name mappings for interrupted threads are part of
the migration callable's job (design note §4).
"""

from __future__ import annotations

from typing import Callable

Migration = Callable[[dict], dict]

NO_MIGRATION = object()

# from_version -> migration (or NO_MIGRATION). Empty while GRAPH_VERSION == 1.
MIGRATIONS: dict[int, Migration | object] = {}


class StrandedThread(Exception):
    """No migration path from the thread's version to the current graph."""

    def __init__(self, from_version: int, to_version: int) -> None:
        super().__init__(f"no migration path from graph version {from_version} to {to_version}")
        self.from_version = from_version
        self.to_version = to_version


def migrate(values: dict, to_version: int) -> dict:
    """Apply forward migrations until `values["graph_version"] == to_version`."""
    current = int(values.get("graph_version") or 0)
    while current < to_version:
        step = MIGRATIONS.get(current)
        if step is None or step is NO_MIGRATION:
            raise StrandedThread(current, to_version)
        values = step(values)  # type: ignore[operator]
        if int(values.get("graph_version") or 0) != current + 1:
            raise RuntimeError(f"migration from v{current} must set graph_version to v{current + 1}")
        current += 1
    return values
