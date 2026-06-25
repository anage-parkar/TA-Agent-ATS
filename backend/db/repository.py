"""Data-access layer (tenant-aware).

Dispatches to Postgres when available, otherwise to an in-memory store so the
app remains runnable before Postgres is set up. Both paths are tenant-scoped:

* Postgres — every call goes through `tenant_connection()`, which sets the
  `app.tenant_id` GUC; RLS policies (migration 0006) filter reads and the
  column default stamps tenant_id on inserts. App-layer filters are therefore
  not the only line of defense.
* In-memory — records are stamped with the current tenant on write and
  filtered by it on read via the `_put` / `_get` / `_vals` helpers.

The current tenant comes from the request-scoped context (services.tenant_context).
Public signatures are identical in both modes and always return plain dicts.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from db.supabase_client import db_available, tenant_connection
from services.tenant_context import current_role, current_user_id, get_tenant_id, use_tenant

# ── In-memory fallback store ──────────────────────────────────────────
_jobs: dict[str, dict] = {}
_candidates: dict[str, dict] = {}
_applications: dict[str, dict] = {}
_emails: dict[str, dict] = {}
_generated_jds: dict[str, dict] = {}
_orgs: dict[str, dict] = {}   # control-plane (not tenant-filtered)
# Workstream B entities
_activity_events: dict[str, dict] = {}
_ai_decisions: dict[str, dict] = {}
_scorecards: dict[str, dict] = {}
_consents: dict[str, dict] = {}
_eeo: dict[str, dict] = {}          # segregated — never read by scoring
_resumes: dict[str, dict] = {}
_pipeline_stages: dict[str, dict] = {}


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


# ── In-memory tenant scoping helpers ──────────────────────────────────
def _put(store: dict[str, dict], rec: dict) -> dict:
    rec["tenant_id"] = get_tenant_id()
    store[rec["id"]] = rec
    return rec


def _owned(rec: dict) -> bool:
    return rec.get("tenant_id") == get_tenant_id()


def _vals(store: dict[str, dict]) -> list[dict]:
    return [r for r in store.values() if _owned(r)]


def _get(store: dict[str, dict], _id: str) -> Optional[dict]:
    rec = store.get(_id)
    return rec if (rec is not None and _owned(rec)) else None


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def identity_keys(row: dict[str, Any]) -> dict[str, str]:
    """Hashed identity signals for dedup / identity resolution.

    A person is the same person across applications if any of these match.
    Hashed so the keys can be indexed/compared without storing raw PII twice.
    """
    keys: dict[str, str] = {}
    email = (row.get("email") or "").strip().lower()
    if email:
        keys["email_hash"] = _sha(email)
    phone = re.sub(r"\D", "", row.get("phone") or "")
    if len(phone) >= 7:
        keys["phone_hash"] = _sha(phone)
    name = re.sub(r"\s+", " ", (row.get("full_name") or "").strip().lower())
    if name:
        keys["name_hash"] = _sha(name)
    return keys


def _emit_audit(
    action: str,
    entity_type: str,
    entity_id: str,
    *,
    before: dict | None = None,
    after: dict | None = None,
    actor_type: str = "human",
    actor_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Write one append-only activity_events row for a state change.

    Best-effort: an audit write must never break the underlying operation.
    """
    try:
        create_activity_event(
            {
                "action": action,
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "before": before,
                "after": after,
                "actor_type": actor_type,
                "actor_id": actor_id if actor_id is not None else current_user_id(),
                "metadata": metadata,
            }
        )
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("ta_agent.repository").exception("audit write failed for %s", action)


# ── Jobs ──────────────────────────────────────────────────────────────
_JOB_FIELDS = (
    "title", "source_url", "raw_html", "skills", "skills_nice_to_have",
    "seniority", "location", "salary_range", "responsibilities", "tech_stack",
)


def create_job(data: dict[str, Any]) -> dict:
    # Normalise so callers can pass a subset (e.g. website jobs = title+skills).
    data = {k: data.get(k) for k in _JOB_FIELDS}

    if not db_available():
        job = {"id": _new_id(), "created_at": _now(), "parsed_at": _now(), **data}
        return _put(_jobs, job)

    with tenant_connection() as conn:
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
            on conflict (tenant_id, source_url) do update set
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
        for j in _vals(_jobs):
            if (j.get("title") or "").strip().lower() == title.lower():
                return j
        return create_job({"title": title, "source_url": None, "skills": []})

    with tenant_connection() as conn:
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
        return _get(_jobs, job_id)
    with tenant_connection() as conn:
        cur = conn.execute("select * from jobs where id = %s", (job_id,))
        return _row(cur)


def set_job_form_id(job_id: str, form_id: str) -> None:
    """Remember which Google/MS Form is linked to this job."""
    if not db_available():
        job = _get(_jobs, job_id)
        if job:
            job["form_id"] = form_id
        return
    with tenant_connection() as conn:
        conn.execute("update jobs set form_id = %s where id = %s", (form_id, job_id))


def get_job_by_source_url(source_url: str) -> Optional[dict]:
    if not db_available():
        for j in _vals(_jobs):
            if j.get("source_url") == source_url:
                return j
        return None
    with tenant_connection() as conn:
        cur = conn.execute("select * from jobs where source_url = %s", (source_url,))
        return _row(cur)


def list_jobs() -> list[dict]:
    if not db_available():
        return sorted(_vals(_jobs), key=lambda j: j["created_at"], reverse=True)
    with tenant_connection() as conn:
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
    keys = identity_keys(data)   # email/phone/name hashes for identity resolution

    if not db_available():
        # Identity resolution within tenant: a person is the same person if
        # linkedin_url, email, or any identity-key (email/phone hash) matches —
        # so one human applying to many jobs stays ONE candidate row.
        lk = row.get("linkedin_url")
        em = (row.get("email") or "").strip().lower()
        match = None
        for c in _vals(_candidates):
            ck = c.get("identity_keys") or {}
            if lk and c.get("linkedin_url") == lk:
                match = c
            elif em and (c.get("email") or "").strip().lower() == em:
                match = c
            elif keys and (
                (keys.get("email_hash") and keys["email_hash"] == ck.get("email_hash"))
                or (keys.get("phone_hash") and keys["phone_hash"] == ck.get("phone_hash"))
            ):
                match = c
            if match:
                break
        if match:
            match.update({k: v for k, v in row.items() if v is not None})
            match["identity_keys"] = {**(match.get("identity_keys") or {}), **keys}
            return match
        cand = {"id": _new_id(), "created_at": _now(), "identity_keys": keys, **row}
        return _put(_candidates, cand)

    row_db = {**row, "identity_keys": json.dumps(keys) if keys else None}
    insert_sql = """
        insert into candidates
          (full_name, linkedin_url, email, phone, headline, skills,
           experience_years, location, resume_url, raw_profile, identity_keys)
        values
          (%(full_name)s, %(linkedin_url)s, %(email)s, %(phone)s,
           %(headline)s, %(skills)s, %(experience_years)s, %(location)s,
           %(resume_url)s, %(raw_profile)s, %(identity_keys)s::jsonb)
    """
    with tenant_connection() as conn:
        if row.get("linkedin_url"):
            cur = conn.execute(
                insert_sql
                + """
                on conflict (tenant_id, linkedin_url) do update set
                  headline = excluded.headline,
                  skills = excluded.skills,
                  email = coalesce(excluded.email, candidates.email),
                  phone = coalesce(excluded.phone, candidates.phone),
                  resume_url = coalesce(excluded.resume_url, candidates.resume_url),
                  identity_keys = coalesce(excluded.identity_keys, candidates.identity_keys)
                returning *
                """,
                row_db,
            )
            return _row(cur)

        # No linkedin_url (e.g. form applicants) — NULLs are distinct in the
        # unique index, so dedupe on email manually (RLS already scopes to tenant).
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

        cur = conn.execute(insert_sql + " returning *", row_db)
        return _row(cur)


def get_candidate(candidate_id: str) -> Optional[dict]:
    if not db_available():
        return _get(_candidates, candidate_id)
    with tenant_connection() as conn:
        cur = conn.execute("select * from candidates where id = %s", (candidate_id,))
        return _row(cur)


def update_candidate_enrichment(candidate_id: str, enrichment: str) -> Optional[dict]:
    """Store scraped enrichment JSON on the candidate."""
    if not db_available():
        cand = _get(_candidates, candidate_id)
        if cand:
            cand["enrichment"] = enrichment
            cand["enriched_at"] = _now()
        return cand
    with tenant_connection() as conn:
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
        for a in _vals(_applications):
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
        created = _put(_applications, app)
        _emit_audit(
            "application.created", "application", created["id"],
            actor_type="system",
            after={"status": created.get("status"), "stage": created.get("stage"),
                   "source": created.get("source")},
        )
        return created

    with tenant_connection() as conn:
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
        created = _row(cur)
        if created:
            _emit_audit(
                "application.created", "application", created["id"],
                actor_type="system",
                after={"status": created.get("status"), "stage": created.get("stage"),
                       "source": created.get("source")},
            )
        return created


def list_applications_for_job(job_id: str) -> list[dict]:
    """Return applications joined with candidate info, ranked by ATS score."""
    if not db_available():
        out = []
        for app in _vals(_applications):
            if app["job_id"] != job_id:
                continue
            cand = _get(_candidates, app["candidate_id"]) or {}
            out.append({**app, "candidate": cand})
        return sorted(out, key=lambda a: a.get("ats_score") or 0, reverse=True)

    with tenant_connection() as conn:
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
        for app in _vals(_applications):
            if app.get("source") not in sources:
                continue
            cand = _get(_candidates, app["candidate_id"]) or {}
            job = _get(_jobs, app["job_id"]) or {}
            out.append(
                {**app, "candidate": cand, "job_title": job.get("title"), "job_id": app["job_id"]}
            )
        return sorted(out, key=lambda a: a.get("ats_score") or -1, reverse=True)

    with tenant_connection() as conn:
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
        for app in _vals(_applications):
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
            job = _get(_jobs, jid) or {}
            out.append({"job_id": jid, "title": job.get("title"), "location": job.get("location"), **d})
        return sorted(out, key=lambda x: x["count"], reverse=True)

    with tenant_connection() as conn:
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
        for j in _vals(_jobs):
            if not str(j.get("source_url") or "").startswith("website:"):
                continue
            apps = [
                a for a in _vals(_applications)
                if a["job_id"] == j["id"] and a.get("source") == "website_portal"
            ]
            out.append({
                "job_id": j["id"], "title": j.get("title"), "location": j.get("location"),
                "count": len(apps),
                "scored": sum(1 for a in apps if a.get("ats_score") is not None),
                "reviewed": sum(1 for a in apps if a.get("status") == "approved"),
            })
        return sorted(out, key=lambda x: x["title"] or "")

    with tenant_connection() as conn:
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
        for app in _vals(_applications):
            counts[app.get("source", "manual")] = counts.get(app.get("source", "manual"), 0) + 1
        return counts
    with tenant_connection() as conn:
        cur = conn.execute("select source, count(*) from applications group by source")
        return {row[0]: row[1] for row in cur.fetchall()}


def get_application_detail(application_id: str) -> Optional[dict]:
    """Full application + candidate + job for the detail view."""
    if not db_available():
        app = _get(_applications, application_id)
        if not app:
            return None
        cand = _get(_candidates, app["candidate_id"]) or {}
        job = _get(_jobs, app["job_id"]) or {}
        return {**app, "candidate": cand, "job_title": job.get("title")}
    with tenant_connection() as conn:
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
        return _put(_emails, em)
    with tenant_connection() as conn:
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
            for e in _vals(_emails)
            if e.get("direction") == "outbound" and e.get("thread_id")
        ]
    with tenant_connection() as conn:
        cur = conn.execute(
            "select distinct thread_id, application_id from emails "
            "where direction = 'outbound' and thread_id is not null"
        )
        return _rows(cur)


def list_emails_for_application(app_id: str) -> list[dict]:
    if not db_available():
        return sorted(
            [e for e in _vals(_emails) if e.get("application_id") == app_id],
            key=lambda e: e.get("created_at") or "",
        )
    with tenant_connection() as conn:
        cur = conn.execute(
            "select * from emails where application_id = %s order by created_at", (app_id,)
        )
        return _rows(cur)


def reply_already_recorded(thread_id: str) -> bool:
    if not db_available():
        return any(
            e.get("thread_id") == thread_id and e.get("direction") == "inbound"
            for e in _vals(_emails)
        )
    with tenant_connection() as conn:
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
        return _put(_generated_jds, jd)

    import json as _json

    with tenant_connection() as conn:
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
        return sorted(_vals(_generated_jds), key=lambda j: j["created_at"], reverse=True)
    with tenant_connection() as conn:
        cur = conn.execute("select * from generated_jds order by created_at desc")
        return _rows(cur)


def get_generated_jd(jd_id: str) -> Optional[dict]:
    if not db_available():
        return _get(_generated_jds, jd_id)
    with tenant_connection() as conn:
        cur = conn.execute("select * from generated_jds where id = %s", (jd_id,))
        return _row(cur)


def update_application(app_id: str, fields: dict[str, Any]) -> Optional[dict]:
    # Capture the prior values of the fields being changed, for the audit trail.
    _audit_keys = [k for k in fields if k in ("status", "stage", "recruiter_decision")]

    if not db_available():
        app = _get(_applications, app_id)
        if not app:
            return None
        before = {k: app.get(k) for k in _audit_keys}
        app.update(fields)
        app["updated_at"] = _now()
        if _audit_keys:
            _emit_audit(
                "application.updated", "application", app_id,
                before=before, after={k: app.get(k) for k in _audit_keys},
            )
        return app

    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    with tenant_connection() as conn:
        before = None
        if _audit_keys:
            prior = _row(conn.execute("select * from applications where id = %s", (app_id,)))
            before = {k: (prior or {}).get(k) for k in _audit_keys}
        cur = conn.execute(
            f"update applications set {sets} where id = %(id)s returning *",
            {**fields, "id": app_id},
        )
        updated = _row(cur)
        if updated and _audit_keys:
            _emit_audit(
                "application.updated", "application", app_id,
                before=before, after={k: updated.get(k) for k in _audit_keys},
            )
        return updated


# ── Activity / audit events (append-only) ─────────────────────────────
def create_activity_event(data: dict[str, Any]) -> dict:
    payload = {
        "actor_type": data.get("actor_type", "system"),
        "actor_id": data.get("actor_id"),
        "action": data["action"],
        "entity_type": data.get("entity_type"),
        "entity_id": data.get("entity_id"),
        "before": data.get("before"),
        "after": data.get("after"),
        "metadata": data.get("metadata"),
    }
    if not db_available():
        ev = {"id": _new_id(), "created_at": _now(), **payload}
        return _put(_activity_events, ev)
    with tenant_connection() as conn:
        cur = conn.execute(
            """
            insert into activity_events
              (actor_type, actor_id, action, entity_type, entity_id, before, after, metadata)
            values
              (%(actor_type)s, %(actor_id)s, %(action)s, %(entity_type)s, %(entity_id)s,
               %(before)s::jsonb, %(after)s::jsonb, %(metadata)s::jsonb)
            returning *
            """,
            {
                **payload,
                "before": json.dumps(payload["before"]) if payload["before"] is not None else None,
                "after": json.dumps(payload["after"]) if payload["after"] is not None else None,
                "metadata": json.dumps(payload["metadata"]) if payload["metadata"] is not None else None,
            },
        )
        return _row(cur)


def list_activity_events(
    entity_type: str | None = None, entity_id: str | None = None
) -> list[dict]:
    if not db_available():
        out = [
            e for e in _vals(_activity_events)
            if (entity_type is None or e.get("entity_type") == entity_type)
            and (entity_id is None or e.get("entity_id") == str(entity_id))
        ]
        return sorted(out, key=lambda e: e.get("created_at") or "")
    clauses, params = [], {}
    if entity_type is not None:
        clauses.append("entity_type = %(et)s")
        params["et"] = entity_type
    if entity_id is not None:
        clauses.append("entity_id = %(eid)s")
        params["eid"] = str(entity_id)
    where = (" where " + " and ".join(clauses)) if clauses else ""
    with tenant_connection() as conn:
        cur = conn.execute(f"select * from activity_events{where} order by created_at", params)
        return _rows(cur)


# ── AI decisions (explainable, versioned) ─────────────────────────────
_AI_DECISION_FIELDS = (
    "application_id", "candidate_id", "job_id", "kind", "model_version",
    "prompt_name", "prompt_version", "prompt_hash", "input_hash", "decision",
    "reviewer_id", "reviewed_at",
)


def create_ai_decision(data: dict[str, Any]) -> dict:
    if not db_available():
        rec = {"id": _new_id(), "created_at": _now(),
               "scores": data.get("scores"), "evidence": data.get("evidence"),
               **{k: data.get(k) for k in _AI_DECISION_FIELDS}}
        return _put(_ai_decisions, rec)
    with tenant_connection() as conn:
        cur = conn.execute(
            """
            insert into ai_decisions
              (application_id, candidate_id, job_id, kind, model_version, prompt_name,
               prompt_version, prompt_hash, input_hash, scores, evidence, decision,
               reviewer_id, reviewed_at)
            values
              (%(application_id)s, %(candidate_id)s, %(job_id)s, %(kind)s, %(model_version)s,
               %(prompt_name)s, %(prompt_version)s, %(prompt_hash)s, %(input_hash)s,
               %(scores)s::jsonb, %(evidence)s::jsonb, %(decision)s, %(reviewer_id)s, %(reviewed_at)s)
            returning *
            """,
            {
                **{k: data.get(k) for k in _AI_DECISION_FIELDS},
                "scores": json.dumps(data.get("scores")) if data.get("scores") is not None else None,
                "evidence": json.dumps(data.get("evidence")) if data.get("evidence") is not None else None,
            },
        )
        return _row(cur)


def list_ai_decisions_for_application(application_id: str) -> list[dict]:
    if not db_available():
        return sorted(
            [d for d in _vals(_ai_decisions) if d.get("application_id") == application_id],
            key=lambda d: d.get("created_at") or "",
        )
    with tenant_connection() as conn:
        cur = conn.execute(
            "select * from ai_decisions where application_id = %s order by created_at",
            (application_id,),
        )
        return _rows(cur)


# ── Scorecards / consent / EEO / resumes / pipeline stages ─────────────
def _simple_insert(table: str, store: dict, fields: tuple, jsonb_fields: tuple, data: dict) -> dict:
    """Insert helper for the straightforward tenant tables."""
    if not db_available():
        rec = {"id": _new_id(), "created_at": _now(), **{k: data.get(k) for k in fields}}
        return _put(store, rec)
    cols = ", ".join(fields)
    vals = ", ".join(
        f"%({k})s::jsonb" if k in jsonb_fields else f"%({k})s" for k in fields
    )
    params = {
        k: (json.dumps(data.get(k)) if (k in jsonb_fields and data.get(k) is not None) else data.get(k))
        for k in fields
    }
    with tenant_connection() as conn:
        cur = conn.execute(
            f"insert into {table} ({cols}) values ({vals}) returning *", params
        )
        return _row(cur)


def create_scorecard(data: dict[str, Any]) -> dict:
    return _simple_insert(
        "scorecards", _scorecards,
        ("application_id", "interviewer_id", "rubric", "ratings", "recommendation", "notes"),
        ("rubric", "ratings"), data,
    )


def list_scorecards_for_application(application_id: str) -> list[dict]:
    if not db_available():
        return [s for s in _vals(_scorecards) if s.get("application_id") == application_id]
    with tenant_connection() as conn:
        cur = conn.execute(
            "select * from scorecards where application_id = %s order by created_at",
            (application_id,),
        )
        return _rows(cur)


def create_consent(data: dict[str, Any]) -> dict:
    return _simple_insert(
        "consent_records", _consents,
        ("candidate_id", "lawful_basis", "scope", "region", "granted_at", "expires_at"),
        (), data,
    )


def list_consents_for_candidate(candidate_id: str) -> list[dict]:
    if not db_available():
        return [c for c in _vals(_consents) if c.get("candidate_id") == candidate_id]
    with tenant_connection() as conn:
        cur = conn.execute(
            "select * from consent_records where candidate_id = %s order by created_at",
            (candidate_id,),
        )
        return _rows(cur)


def create_eeo_record(data: dict[str, Any]) -> dict:
    """Voluntary diversity data — SEGREGATED. Nothing in the scoring path reads
    this; it exists only for aggregate adverse-impact reporting (Workstream F)."""
    return _simple_insert("eeo_records", _eeo, ("candidate_id", "data"), ("data",), data)


def create_resume(data: dict[str, Any]) -> dict:
    return _simple_insert(
        "resumes", _resumes,
        ("candidate_id", "file_url", "parsed_profile", "redacted_profile"),
        ("parsed_profile", "redacted_profile"), data,
    )


def get_resume_for_candidate(candidate_id: str) -> Optional[dict]:
    if not db_available():
        for r in _vals(_resumes):
            if r.get("candidate_id") == candidate_id:
                return r
        return None
    with tenant_connection() as conn:
        cur = conn.execute(
            "select * from resumes where candidate_id = %s order by created_at desc limit 1",
            (candidate_id,),
        )
        return _row(cur)


def create_pipeline_stage(data: dict[str, Any]) -> dict:
    return _simple_insert(
        "pipeline_stages", _pipeline_stages,
        ("job_id", "name", "position", "transitions"), ("transitions",), data,
    )


def list_pipeline_stages(job_id: str) -> list[dict]:
    if not db_available():
        return sorted(
            [s for s in _vals(_pipeline_stages) if s.get("job_id") == job_id],
            key=lambda s: s.get("position") or 0,
        )
    with tenant_connection() as conn:
        cur = conn.execute(
            "select * from pipeline_stages where job_id = %s order by position", (job_id,)
        )
        return _rows(cur)


# ── Tenant data lifecycle (export / delete) ───────────────────────────
# Independent per-tenant export and purge (Workstream A acceptance + supports
# Workstream F right-to-erasure / portability). Both bind the target tenant so
# RLS (DB) / the in-memory filter scope every row to that tenant only.

_DOMAIN_STORES = {
    "jobs": _jobs,
    "candidates": _candidates,
    "applications": _applications,
    "emails": _emails,
    "generated_jds": _generated_jds,
    "activity_events": _activity_events,
    "ai_decisions": _ai_decisions,
    "scorecards": _scorecards,
    "consent_records": _consents,
    "eeo_records": _eeo,
    "resumes": _resumes,
    "pipeline_stages": _pipeline_stages,
}
# DB delete order respects FK dependencies (children first).
_DELETE_ORDER = (
    "activity_events", "ai_decisions", "scorecards", "consent_records",
    "eeo_records", "resumes", "pipeline_stages",
    "applications", "emails", "interviews", "job_members",
    "generated_jds", "candidates", "jobs",
)
_EXPORT_TABLES = (
    "jobs", "candidates", "applications", "emails", "interviews",
    "generated_jds", "job_members", "memberships",
    "pipeline_stages", "activity_events", "ai_decisions", "scorecards",
    "consent_records", "eeo_records", "resumes",
)


def export_tenant(tenant_id: str) -> dict[str, list[dict]]:
    """Return every row owned by `tenant_id`, grouped by table."""
    if not db_available():
        with use_tenant(tenant_id):
            return {name: list(_vals(store)) for name, store in _DOMAIN_STORES.items()}

    out: dict[str, list[dict]] = {}
    with use_tenant(tenant_id), tenant_connection() as conn:
        for table in _EXPORT_TABLES:
            cur = conn.execute(f"select * from {table}")  # RLS-scoped to tenant
            out[table] = _rows(cur)
    return out


def delete_tenant(tenant_id: str) -> dict[str, int]:
    """Purge all domain data for `tenant_id`. Returns rows deleted per table.

    Keeps the organization/users rows; this is a data purge, not org deletion.
    """
    if not db_available():
        deleted: dict[str, int] = {}
        for name, store in _DOMAIN_STORES.items():
            ids = [k for k, v in store.items() if v.get("tenant_id") == tenant_id]
            for k in ids:
                del store[k]
            deleted[name] = len(ids)
        return deleted

    deleted = {}
    with use_tenant(tenant_id), tenant_connection() as conn:
        for table in _DELETE_ORDER:
            cur = conn.execute(f"delete from {table}")  # RLS-scoped to tenant
            deleted[table] = cur.rowcount
    return deleted


# ── Organizations / memberships (control plane) ───────────────────────
def create_organization(name: str, slug: str | None = None, region: str = "global") -> dict:
    """Create a tenant. Control-plane table (no RLS); admin-gated at the router."""
    if not db_available():
        org = {"id": _new_id(), "name": name, "slug": slug, "region": region, "created_at": _now()}
        _orgs[org["id"]] = org
        return org
    with tenant_connection() as conn:
        cur = conn.execute(
            "insert into organizations (name, slug, region) values (%s, %s, %s) returning *",
            (name, slug, region),
        )
        return _row(cur)
