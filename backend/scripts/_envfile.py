"""Minimal KEY=VALUE env-file reader/updater shared by the Gmail test tools.

Deliberately tiny and dependency-free: it only has to preserve a hand-edited
file while creating or replacing one key, and must never echo values.
"""

from __future__ import annotations

import os
import pathlib
import stat


def read_env_file(path: pathlib.Path) -> dict[str, str]:
    """Parse KEY=VALUE lines; blank lines and # comments are ignored; a single
    layer of matching quotes around the value is stripped."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def upsert_env_value(path: pathlib.Path, key: str, value: str) -> None:
    """Create or update exactly one KEY=VALUE line, preserving every other
    line (comments, order, unrelated keys). If the key appears more than once,
    the first occurrence is replaced and the rest removed. The file is created
    owner-read/write only where the platform supports it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and "=" in stripped and stripped.split("=", 1)[0].strip() == key:
            if not replaced:
                out.append(f"{key}={value}")
                replaced = True
            continue  # drop duplicates
        out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover — Windows ACLs; the directory is gitignored regardless
        pass
