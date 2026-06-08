"""Official website / careers-portal channel.

Two responsibilities:
  1. fetch_active_jobs() — list the active jobs published on your careers site
     (or the external ATS that powers it), so they show in the Website channel.
  2. forward_to_partner() — the fan-out: when someone applies through your
     portal, TA Agent ingests them AND forwards the application to the external
     partner ATS you already use, so both systems get the candidate.

Both are pluggable. Until you provide your careers feed URL / partner endpoint,
mock data is used so the channel works end-to-end today.
"""

from __future__ import annotations

import logging

import httpx

from services.config import settings

logger = logging.getLogger("ta_agent.website")


CEIPAL_BASE = "https://careerapi.ceipal.com"


def _ceipal_configured() -> bool:
    return bool(settings.ceipal_api_key and settings.ceipal_cp_id)


def _use_mock() -> bool:
    if _ceipal_configured():
        return False
    return settings.use_mock_website or not settings.website_careers_api_url


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    import re

    return re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ").strip()


_TECH = [
    "Python", "Java", "Spring", "Springboot", "Kafka", "FastAPI", "Django", "Go",
    "Node.js", "React", "Angular", "TypeScript", "JavaScript", "PostgreSQL", "MySQL",
    "MongoDB", "Redis", "Docker", "Kubernetes", "AWS", "Azure", "GCP", "Terraform",
    "Microservices", "Spark", "Airflow", "PyTorch", "TensorFlow", "Snowflake", "Databricks",
]


def _skills_from_text(text: str | None) -> list[str]:
    t = (text or "").lower()
    return [kw for kw in _TECH if kw.lower() in t]


def _fetch_ceipal_jobs() -> list[dict]:
    """Pull active job postings from the Ceipal career portal API."""
    key, cp = settings.ceipal_api_key, settings.ceipal_cp_id
    headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Referer-Host": "https://jobsapi.ceipal.com/",
        "Referer": "https://jobsapi.ceipal.com/",
        "Origin": "https://jobsapi.ceipal.com",
    }
    out: list[dict] = []
    page = 1
    while page <= 10:  # safety cap
        url = f"{CEIPAL_BASE}/{key}/CareerPortalJobPostings/?page={page}"
        form = {
            "api_key": key,
            "cp_id": cp,
            "method": "CareerPortalJobPostings",
            "from_career_portal": "1",
            "page": str(page),
        }
        resp = httpx.post(
            url, files={k: (None, v) for k, v in form.items()}, headers=headers, timeout=40
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", []) if isinstance(data, dict) else []
        for j in results:
            title = j.get("public_job_title") or j.get("position_title") or ""
            desc = _strip_html(j.get("public_job_desc") or j.get("requistion_description"))
            location = j.get("city") or ("Remote" if j.get("remote_opportunities") else None)
            out.append(
                {
                    "external_id": j.get("id") or j.get("job_code"),
                    "title": title,
                    "location": location,
                    "skills": _skills_from_text(desc),
                    "description": desc,
                    "apply_url": f"https://www.parkar.in/open-position?job_id={j.get('id')}",
                }
            )
        num_pages = (data.get("num_pages") if isinstance(data, dict) else 1) or 1
        if page >= num_pages:
            break
        page += 1
    logger.info("Website jobs (Ceipal): %d", len(out))
    return out


# ── Mock active jobs (replace with your real careers feed) ────────────
_MOCK_JOBS = [
    {
        "external_id": "web-1001",
        "title": "Senior Backend Engineer",
        "location": "Remote, India",
        "skills": ["Python", "FastAPI", "PostgreSQL", "AWS", "Redis"],
        "description": "Own and ship backend services for our platform.",
    },
    {
        "external_id": "web-1002",
        "title": "Frontend Engineer",
        "location": "Pune, India",
        "skills": ["TypeScript", "React", "Next.js", "Tailwind"],
        "description": "Build delightful product UI in Next.js.",
    },
    {
        "external_id": "web-1003",
        "title": "DevOps Engineer",
        "location": "Remote",
        "skills": ["AWS", "Terraform", "Kubernetes", "CI/CD"],
        "description": "Run our cloud infra and delivery pipelines.",
    },
]


def fetch_active_jobs() -> list[dict]:
    """Return active jobs from the careers site / external ATS."""
    if _ceipal_configured():
        return _fetch_ceipal_jobs()
    if _use_mock():
        logger.info("Website jobs (mock): %d", len(_MOCK_JOBS))
        return _MOCK_JOBS

    # CONFIRM the shape against your careers/ATS feed and map fields below.
    resp = httpx.get(settings.website_careers_api_url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("jobs", data) if isinstance(data, dict) else data
    out = []
    for j in items:
        out.append(
            {
                "external_id": str(j.get("id") or j.get("external_id") or j.get("slug")),
                "title": j.get("title") or j.get("name", ""),
                "location": j.get("location"),
                "skills": j.get("skills") or [],
                "description": j.get("description"),
            }
        )
    return out


def forward_to_partner(application: dict) -> dict:
    """Fan-out: forward a portal application to the external partner ATS.

    Best-effort and never blocks ingestion — if it fails, TA Agent still keeps
    the candidate and we log the failure for retry.
    """
    if not settings.website_partner_forward_url:
        logger.info("No WEBSITE_PARTNER_FORWARD_URL set — skipping partner forward.")
        return {"forwarded": False, "reason": "no partner endpoint configured"}
    try:
        resp = httpx.post(settings.website_partner_forward_url, json=application, timeout=20)
        resp.raise_for_status()
        return {"forwarded": True, "status": resp.status_code}
    except Exception as exc:  # noqa: BLE001 — never fail the applicant ingest
        logger.warning("Partner forward failed: %s", exc)
        return {"forwarded": False, "reason": str(exc)}
