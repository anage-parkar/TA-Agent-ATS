"""Minimal DB layer for the LinkedIn MCP server.

Writes to the same Postgres (cloud Supabase) the TA backend uses, via psycopg
and DATABASE_URL. Kept self-contained so the MCP server can run as its own
process without importing the backend package.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
# If the MCP .env didn't set DATABASE_URL (left blank), fall back to the
# repo-root .env so we share the backend's connection. override=True is needed
# because the blank value above already put an empty string into the env.
if not os.getenv("DATABASE_URL"):
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

DATABASE_URL = os.getenv("DATABASE_URL")


def _connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set (point it at your Supabase pooler).")
    return psycopg.connect(DATABASE_URL, connect_timeout=15)


def _one(cur) -> dict | None:
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip([c.name for c in cur.description], row))


def upsert_job_from_linkedin(job: dict) -> dict:
    """Map a LinkedIn job posting to the jobs table and upsert it."""
    job_id = job.get("id")
    record = {
        "title": job.get("title", ""),
        "source_url": f"https://www.linkedin.com/jobs/view/{job_id}",
        "raw_html": json.dumps(job),
        "skills": job.get("skills") or [],
        "location": job.get("formattedLocation"),
        "tech_stack": job.get("skills") or [],
        "responsibilities": [],
    }
    with _connect() as conn:
        cur = conn.execute(
            """
            insert into jobs (title, source_url, raw_html, skills, location,
                              tech_stack, responsibilities, parsed_at)
            values (%(title)s, %(source_url)s, %(raw_html)s, %(skills)s,
                    %(location)s, %(tech_stack)s, %(responsibilities)s, now())
            on conflict (source_url) do update set
                title = excluded.title, skills = excluded.skills, parsed_at = now()
            returning id, title, source_url
            """,
            record,
        )
        return _one(cur)


def upsert_applicant(applicant: dict) -> dict:
    """Map a LinkedIn applicant to the candidates table and upsert it."""
    full_name = f"{applicant.get('firstName','')} {applicant.get('lastName','')}".strip()
    record = {
        "full_name": full_name or applicant.get("name", "Unknown"),
        "linkedin_url": applicant.get("linkedinProfile"),
        "email": applicant.get("email"),
        "headline": applicant.get("headline"),
        "skills": applicant.get("skills") or [],
        "experience_years": applicant.get("experienceYears"),
        "location": applicant.get("location"),
        "raw_profile": json.dumps(applicant),
    }
    with _connect() as conn:
        cur = conn.execute(
            """
            insert into candidates (full_name, linkedin_url, email, headline,
                                    skills, experience_years, location, raw_profile)
            values (%(full_name)s, %(linkedin_url)s, %(email)s, %(headline)s,
                    %(skills)s, %(experience_years)s, %(location)s, %(raw_profile)s)
            on conflict (linkedin_url) do update set
                headline = excluded.headline, skills = excluded.skills,
                email = coalesce(excluded.email, candidates.email)
            returning id, full_name
            """,
            record,
        )
        return _one(cur)


def create_application(job_id: str, candidate_id: str, status: str = "applied") -> dict:
    """Create the candidate×job application row (idempotent)."""
    with _connect() as conn:
        cur = conn.execute(
            """
            insert into applications (job_id, candidate_id, status)
            values (%s, %s, %s)
            on conflict (job_id, candidate_id) do update set status = excluded.status
            returning id, status
            """,
            (job_id, candidate_id, status),
        )
        return _one(cur)


def find_job_id_by_linkedin(job_linkedin_id: str) -> str | None:
    url = f"https://www.linkedin.com/jobs/view/{job_linkedin_id}"
    with _connect() as conn:
        cur = conn.execute("select id from jobs where source_url = %s", (url,))
        row = cur.fetchone()
        return str(row[0]) if row else None
