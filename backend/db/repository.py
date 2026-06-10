"""Data-access layer.

Dispatches to Postgres when available, otherwise to an in-memory store so the
app remains runnable before Docker/Postgres is set up. The public function
signatures are identical in both modes and always return plain dicts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from db.supabase_client import db_available, get_pool

# ── In-memory fallback store ──────────────────────────────────────────
_jobs: dict[str, dict] = {}
_candidates: dict[str, dict] = {}
_applications: dict[str, dict] = {}
_emails: dict[str, dict] = {}
_generated_jds: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _rows(cur) -> list[dict]:
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _row(cur) -> Optional[dict]:
    rows = _rows(cur)
    return rows[0] if rows else None


# ── Jobs ──────────────────────────────────────────────────────────────
_JOB_FIELDS = (
    "title", "source_url", "raw_html", "skills", "skills_nice_to_have",
    "seniority", "location", "salary_range", "responsibilities", "tech_stack",
)


def create_job(data: dict[str, Any]) -> dict:
    # Normalise so callers can pass a subset (e.g. website jobs = title+skills).
    data = {k: data.get(k) for k in _JOB_FIELDS}

    if not db_available():
        job = {
            "id": _new_id(),
            "created_at": _now(),
            "parsed_at": _now(),
            **data,
        }
        _jobs[job["id"]] = job
        return job

    with get_pool().connection() as conn:
        cur = conn.execute(
            """
            insert into jobs
              (title, source_url, raw_html, skills, skills_nice_to_have,
               seniority, location, salary_range, responsibilities, tech_stack,
               parsed_at)
            values
              (%(title)s, %(source_url)s, %(raw_html)s, %(skills)s,
               %(skills_nice_to_have)s, %(seniority)s, %(location)s,
               %(salary_range)s, %(responsibilities)s, %(tech_stack)s, now())
            on conflict (source_url) do update set
              title = excluded.title,
              skills = excluded.skills,
              parsed_at = now()
            returning *
            """,
            data,
        )
        return _row(cur)


def find_or_create_job_by_title(title: str) -> dict:
    """Return an existing job with this title (case-insensitive) or create a
    minimal one. Lets the UI take a typed position instead of a job picker, and
    keeps repeated syncs/hunts from spawning duplicate jobs."""
    title = (title or "").strip()
    if not title:
        raise ValueError("Job title is required.")

    if not db_available():
        for j in _jobs.values():
            if (j.get("title") or "").strip().lower() == title.lower():
                return j
        return create_job({"title": title, "source_url": None, "skills": []})

    with get_pool().connection() as conn:
        cur = conn.execute(
            "select * from jobs where lower(title) = lower(%s) order by created_at limit 1",
            (title,),
        )
        existing = _row(cur)
        if existing:
            return existing
        cur = conn.execute("insert into jobs (title) values (%s) returning *", (title,))
        return _row(cur)


def get_job(job_id: str) -> Optional[dict]:
    if not db_available():
        return _jobs.get(job_id)
    with get_pool().connection() as conn:
        cur = conn.execute("select * from jobs where id = %s", (job_id,))
        return _row(cur)


def set_job_form_id(job_id: str, form_id: str) -> None:
    """Remember which Google/MS Form is linked to this job."""
    if not db_available():
        if job_id in _jobs:
            _jobs[job_id]["form_id"] = form_id
        return
    with get_pool().connection() as conn:
        conn.execute("update jobs set form_id = %s where id = %s", (form_id, job_id))


def get_job_by_source_url(source_url: str) -> Optional[dict]:
    if not db_available():
        for j in _jobs.values():
            if j.get("source_url") == source_url:
                return j
        return None
    with get_pool().connection() as conn:
        cur = conn.execute("select * from jobs where source_url = %s", (source_url,))
        return _row(cur)


def list_jobs() -> list[dict]:
    if not db_available():
        return sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)
    with get_pool().connection() as conn:
        cur = conn.execute("select * from jobs order by created_at desc")
        return _rows(cur)


# ── Candidates ────────────────────────────────────────────────────────
_CANDIDATE_FIELDS = (
    "full_name",
    "linkedin_url",
    "email",
    "phone",
    "headline",
    "skills",
    "experience_years",
    "location",
    "resume_url",
    "raw_profile",
)


def upsert_candidate(data: dict[str, Any]) -> dict:
    # Only persist known columns; tolerate callers passing a subset.
    row = {k: data.get(k) for k in _CANDIDATE_FIELDS}

    if not db_available():
        # de-dupe on linkedin_url (when present), else on email
        key = row.get("linkedin_url") or row.get("email")
        if key:
            for c in _candidates.values():
                if (c.get("linkedin_url") or c.get("email")) == key:
                    c.update({k: v for k, v in row.items() if v is not None})
                    return c
        cand = {"id": _new_id(), "created_at": _now(), **row}
        _candidates[cand["id"]] = cand
        return cand

    insert_sql = """
        insert into candidates
          (full_name, linkedin_url, email, phone, headline, skills,
           experience_years, location, resume_url, raw_profile)
        values
          (%(full_name)s, %(linkedin_url)s, %(email)s, %(phone)s,
           %(headline)s, %(skills)s, %(experience_years)s, %(location)s,
           %(resume_url)s, %(raw_profile)s)
    """
    with get_pool().connection() as conn:
        if row.get("linkedin_url"):
            cur = conn.execute(
                insert_sql
                + """
                on conflict (linkedin_url) do update set
                  headline = excluded.headline,
                  skills = excluded.skills,
                  email = coalesce(excluded.email, candidates.email),
                  phone = coalesce(excluded.phone, candidates.phone),
                  resume_url = coalesce(excluded.resume_url, candidates.resume_url)
                returning *
                """,
                row,
            )
            return _row(cur)

        # No linkedin_url (e.g. form applicants) — there's no unique index to
        # conflict on (NULLs are distinct), so dedupe on email manually to avoid
        # creating a new row every re-sync.
        if row.get("email"):
            existing = _row(
                conn.execute(
                    "select * from candidates where email = %s order by created_at limit 1",
                    (row["email"],),
                )
            )
            if existing:
                cur = conn.execute(
                    """
                    update candidates set
                      full_name = %(full_name)s,
                      phone = coalesce(%(phone)s, phone),
                      headline = coalesce(%(headline)s, headline),
                      skills = %(skills)s,
                      experience_years = coalesce(%(experience_years)s, experience_years),
                      location = coalesce(%(location)s, location),
                      resume_url = coalesce(%(resume_url)s, resume_url)
                    where id = %(id)s
                    returning *
                    """,
                    {**row, "id": existing["id"]},
                )
                return _row(cur)

        cur = conn.execute(insert_sql + " returning *", row)
        return _row(cur)


def get_candidate(candidate_id: str) -> Optional[dict]:
    if not db_available():
        return _candidates.get(candidate_id)
    with get_pool().connection() as conn:
        cur = conn.execute("select * from candidates where id = %s", (candidate_id,))
        return _row(cur)


def update_candidate_enrichment(candidate_id: str, enrichment: str) -> Optional[dict]:
    """Store scraped enrichment JSON on the candidate."""
    if not db_available():
        cand = _candidates.get(candidate_id)
        if cand:
            cand["enrichment"] = enrichment
            cand["enriched_at"] = _now()
        return cand
    with get_pool().connection() as conn:
        cur = conn.execute(
            "update candidates set enrichment = %s, enriched_at = now() where id = %s returning *",
            (enrichment, candidate_id),
        )
        return _row(cur)


# ── Applications ──────────────────────────────────────────────────────
def create_application(data: dict[str, Any]) -> dict:
    if not db_available():
        # de-dupe on (job_id, candidate_id). Preserve a recruiter decision —
        # a re-sync must never resurrect a rejected/approved application.
        for a in _applications.values():
            if a["job_id"] == data["job_id"] and a["candidate_id"] == data["candidate_id"]:
                for k, v in data.items():
                    if k in ("status", "source", "recruiter_decision"):
                        continue
                    a[k] = v
                a["updated_at"] = _now()
                return a
        app = {
            "id": _new_id(),
            "status": data.get("status", "sourced"),
            "source": data.get("source", "manual"),
            "ats_score": data.get("ats_score"),
            "ats_breakdown": data.get("ats_breakdown"),
            "stage": data.get("stage", "Sourced"),
            "recruiter_decision": None,
            "created_at": _now(),
            "updated_at": _now(),
            **data,
        }
        _applications[app["id"]] = app
        return app

    with get_pool().connection() as conn:
        cur = conn.execute(
            """
            insert into applications
              (job_id, candidate_id, ats_score, ats_breakdown, status, source, stage)
            values
              (%(job_id)s, %(candidate_id)s, %(ats_score)s,
               %(ats_breakdown)s, %(status)s, %(source)s, %(stage)s)
            on conflict (job_id, candidate_id) do update set
              ats_score = coalesce(excluded.ats_score, applications.ats_score),
              ats_breakdown = coalesce(excluded.ats_breakdown, applications.ats_breakdown)
            returning *
            """,
            {
                "status": "sourced",
                "source": "manual",
                "ats_score": None,
                "ats_breakdown": None,
                "stage": "Sourced",
                **data,
            },
        )
        return _row(cur)


def list_applications_for_job(job_id: str) -> list[dict]:
    """Return applications joined with candidate info, ranked by ATS score."""
    if not db_available():
        out = []
        for app in _applications.values():
            if app["job_id"] != job_id:
                continue
            cand = _candidates.get(app["candidate_id"], {})
            out.append({**app, "candidate": cand})
        return sorted(out, key=lambda a: a.get("ats_score") or 0, reverse=True)

    with get_pool().connection() as conn:
        cur = conn.execute(
            """
            select a.*, row_to_json(c.*) as candidate
            from applications a
            join candidates c on c.id = a.candidate_id
            where a.job_id = %s
            order by a.ats_score desc nulls last
            """,
            (job_id,),
        )
        return _rows(cur)


def list_applications_by_sources(sources: list[str]) -> list[dict]:
    """All applications whose source is in `sources`, across every job,
    joined with candidate + job title. Ranked by score."""
    if not db_available():
        out = []
        for app in _applications.values():
            if app.get("source") not in sources:
                continue
            cand = _candidates.get(app["candidate_id"], {})
            job = _jobs.get(app["job_id"], {})
            out.append(
                {**app, "candidate": cand, "job_title": job.get("title"), "job_id": app["job_id"]}
            )
        return sorted(out, key=lambda a: a.get("ats_score") or -1, reverse=True)

    with get_pool().connection() as conn:
        cur = conn.execute(
            """
            select a.*, row_to_json(c.*) as candidate, j.title as job_title
            from applications a
            join candidates c on c.id = a.candidate_id
            join jobs j on j.id = a.job_id
            where a.source = any(%s)
            order by a.ats_score desc nulls last
            """,
            (sources,),
        )
        return _rows(cur)


def list_jobs_with_channel_counts(sources: list[str]) -> list[dict]:
    """Jobs that have applications in the given channel, with counts."""
    if not db_available():
        agg: dict[str, dict] = {}
        for app in _applications.values():
            if app.get("source") not in sources:
                continue
            jid = app["job_id"]
            d = agg.setdefault(jid, {"count": 0, "scored": 0, "reviewed": 0})
            d["count"] += 1
            if app.get("ats_score") is not None:
                d["scored"] += 1
            if app.get("status") == "approved":
                d["reviewed"] += 1
        out = []
        for jid, d in agg.items():
            job = _jobs.get(jid, {})
            out.append({"job_id": jid, "title": job.get("title"), "location": job.get("location"), **d})
        return sorted(out, key=lambda x: x["count"], reverse=True)

    with get_pool().connection() as conn:
        cur = conn.execute(
            """
            select j.id as job_id, j.title, j.location,
                   count(a.*) as count,
                   count(a.ats_score) as scored,
                   count(*) filter (where a.status = 'approved') as reviewed
            from applications a
            join jobs j on j.id = a.job_id
            where a.source = any(%s)
            group by j.id, j.title, j.location
            order by count(a.*) desc
            """,
            (sources,),
        )
        return _rows(cur)


def list_website_jobs() -> list[dict]:
    """All jobs that originated from the careers website (source_url 'website:%'),
    including those with zero applicants yet, with website_portal counts."""
    if not db_available():
        out = []
        for j in _jobs.values():
            if not str(j.get("source_url") or "").startswith("website:"):
                continue
            apps = [a for a in _applications.values() if a["job_id"] == j["id"] and a.get("source") == "website_portal"]
            out.append({
                "job_id": j["id"], "title": j.get("title"), "location": j.get("location"),
                "count": len(apps),
                "scored": sum(1 for a in apps if a.get("ats_score") is not None),
                "reviewed": sum(1 for a in apps if a.get("status") == "approved"),
            })
        return sorted(out, key=lambda x: x["title"] or "")

    with get_pool().connection() as conn:
        cur = conn.execute(
            """
            select j.id as job_id, j.title, j.location,
                   count(a.*) filter (where a.source = 'website_portal') as count,
                   count(a.ats_score) filter (where a.source = 'website_portal') as scored,
                   count(*) filter (where a.source = 'website_portal' and a.status = 'approved') as reviewed
            from jobs j
            left join applications a on a.job_id = j.id
            where j.source_url like 'website:%'
            group by j.id, j.title, j.location
            order by j.title
            """
        )
        return _rows(cur)


def count_applications_by_source() -> dict[str, int]:
    """Map of source -> application count (across all jobs)."""
    if not db_available():
        counts: dict[str, int] = {}
        for app in _applications.values():
            counts[app.get("source", "manual")] = counts.get(app.get("source", "manual"), 0) + 1
        return counts
    with get_pool().connection() as conn:
        cur = conn.execute("select source, count(*) from applications group by source")
        return {row[0]: row[1] for row in cur.fetchall()}


def get_application_detail(application_id: str) -> Optional[dict]:
    """Full application + candidate + job for the detail view."""
    if not db_available():
        app = _applications.get(application_id)
        if not app:
            return None
        cand = _candidates.get(app["candidate_id"], {})
        job = _jobs.get(app["job_id"], {})
        return {**app, "candidate": cand, "job_title": job.get("title")}
    with get_pool().connection() as conn:
        cur = conn.execute(
            """
            select a.*, row_to_json(c.*) as candidate, j.title as job_title
            from applications a
            join candidates c on c.id = a.candidate_id
            join jobs j on j.id = a.job_id
            where a.id = %s
            """,
            (application_id,),
        )
        return _row(cur)


# ── Emails ────────────────────────────────────────────────────────────
def create_email(data: dict[str, Any]) -> dict:
    if not db_available():
        em = {"id": _new_id(), "created_at": _now(), **data}
        _emails[em["id"]] = em
        return em
    with get_pool().connection() as conn:
        cur = conn.execute(
            """
            insert into emails
              (application_id, direction, subject, body, sent_at, replied_at,
               intent, raw_reply, thread_id)
            values
              (%(application_id)s, %(direction)s, %(subject)s, %(body)s,
               %(sent_at)s, %(replied_at)s, %(intent)s, %(raw_reply)s, %(thread_id)s)
            returning *
            """,
            {
                "application_id": data.get("application_id"),
                "direction": data.get("direction"),
                "subject": data.get("subject"),
                "body": data.get("body"),
                "sent_at": data.get("sent_at"),
                "replied_at": data.get("replied_at"),
                "intent": data.get("intent"),
                "raw_reply": data.get("raw_reply"),
                "thread_id": data.get("thread_id"),
            },
        )
        return _row(cur)


def list_outbound_threads() -> list[dict]:
    """Outbound emails that have a thread_id → {thread_id, application_id}."""
    if not db_available():
        return [
            {"thread_id": e["thread_id"], "application_id": e["application_id"]}
            for e in _emails.values()
            if e.get("direction") == "outbound" and e.get("thread_id")
        ]
    with get_pool().connection() as conn:
        cur = conn.execute(
            "select distinct thread_id, application_id from emails "
            "where direction = 'outbound' and thread_id is not null"
        )
        return _rows(cur)


def list_emails_for_application(app_id: str) -> list[dict]:
    if not db_available():
        return sorted(
            [e for e in _emails.values() if e.get("application_id") == app_id],
            key=lambda e: e.get("created_at") or "",
        )
    with get_pool().connection() as conn:
        cur = conn.execute(
            "select * from emails where application_id = %s order by created_at", (app_id,)
        )
        return _rows(cur)


def reply_already_recorded(thread_id: str) -> bool:
    if not db_available():
        return any(
            e.get("thread_id") == thread_id and e.get("direction") == "inbound"
            for e in _emails.values()
        )
    with get_pool().connection() as conn:
        cur = conn.execute(
            "select 1 from emails where thread_id = %s and direction = 'inbound' limit 1",
            (thread_id,),
        )
        return cur.fetchone() is not None


# ── Generated JDs ─────────────────────────────────────────────────────
def create_generated_jd(data: dict[str, Any]) -> dict:
    """Persist a generated JD (content + optional PDF base64) and return it."""
    if not db_available():
        jd = {
            "id": data["id"],
            "business_unit": data["business_unit"],
            "role": data["role"],
            "designation": data["designation"],
            "years_of_experience": data["years_of_experience"],
            "skills": data.get("skills", []),
            "content": data.get("content", {}),
            "pdf_base64": data.get("pdf_base64"),
            "pdf_url": data.get("pdf_url"),
            "created_at": data.get("created_at", _now()),
        }
        _generated_jds[jd["id"]] = jd
        return jd

    import json as _json

    with get_pool().connection() as conn:
        cur = conn.execute(
            """
            insert into generated_jds
              (id, business_unit, role, designation, years_of_experience,
               skills, content, pdf_base64, pdf_url, created_at)
            values
              (%(id)s, %(business_unit)s, %(role)s, %(designation)s,
               %(years_of_experience)s, %(skills)s, %(content)s,
               %(pdf_base64)s, %(pdf_url)s, %(created_at)s)
            returning *
            """,
            {
                "id": data["id"],
                "business_unit": data["business_unit"],
                "role": data["role"],
                "designation": data["designation"],
                "years_of_experience": data["years_of_experience"],
                "skills": _json.dumps(data.get("skills", [])),
                "content": _json.dumps(data.get("content", {})),
                "pdf_base64": data.get("pdf_base64"),
                "pdf_url": data.get("pdf_url"),
                "created_at": data.get("created_at", _now()),
            },
        )
        return _row(cur)


def list_generated_jds() -> list[dict]:
    """Return all generated JDs newest-first."""
    if not db_available():
        return sorted(_generated_jds.values(), key=lambda j: j["created_at"], reverse=True)

    with get_pool().connection() as conn:
        cur = conn.execute(
            "select * from generated_jds order by created_at desc"
        )
        return _rows(cur)


def get_generated_jd(jd_id: str) -> Optional[dict]:
    if not db_available():
        return _generated_jds.get(jd_id)

    with get_pool().connection() as conn:
        cur = conn.execute(
            "select * from generated_jds where id = %s", (jd_id,)
        )
        return _row(cur)


def update_application(app_id: str, fields: dict[str, Any]) -> Optional[dict]:
    if not db_available():
        app = _applications.get(app_id)
        if not app:
            return None
        app.update(fields)
        app["updated_at"] = _now()
        return app

    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    with get_pool().connection() as conn:
        cur = conn.execute(
            f"update applications set {sets} where id = %(id)s returning *",
            {**fields, "id": app_id},
        )
        return _row(cur)
