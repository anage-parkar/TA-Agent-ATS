"""One-time migration: create the generated_jds table in Supabase.

Run once from the backend/ directory:
    python scripts/migrate_jds.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow imports from the backend package root
_backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend_dir))

# Load .env from backend/ (pydantic-settings will pick it up via config.py,
# but the config resolves the path relative to config.py's location which
# points to the repo root — not backend/. We pre-load it here so the
# DATABASE_URL env-var is set before the settings singleton is created.)
_env_file = _backend_dir / ".env"
if _env_file.exists():
    from dotenv import dotenv_values
    for k, v in dotenv_values(str(_env_file)).items():
        if v and k not in os.environ:
            os.environ[k] = v

from db.supabase_client import db_available, get_pool

SQL = """
create table if not exists generated_jds (
    id           uuid primary key default gen_random_uuid(),
    business_unit text not null,
    role          text not null,
    designation   text not null,
    years_of_experience integer not null,
    skills        jsonb not null default '[]',
    content       jsonb not null,
    pdf_base64    text,
    pdf_url       text,
    created_at    timestamptz not null default now()
);
"""


def main() -> None:
    if not db_available():
        print("ERROR: Database is not reachable. Check DATABASE_URL in .env")
        sys.exit(1)

    with get_pool().connection() as conn:
        conn.execute(SQL)
        print("generated_jds table created (or already existed).")


if __name__ == "__main__":
    main()
