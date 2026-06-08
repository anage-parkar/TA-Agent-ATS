"""Thin wrapper over the LinkedIn Talent Solutions REST API.

Covers two partner-gated surfaces:
  - Job Posting API   — read/create your org's job postings
  - Apply Connect     — read applicants who applied to those postings

Until LinkedIn partner approval lands (and you can confirm the exact endpoints
+ scopes against the partner docs / Postman collection), set
USE_MOCK_LINKEDIN=true to exercise the whole pipeline with canned data.

⚠️ Endpoints marked CONFIRM are best-effort placeholders. The Job Posting paths
are public-doc shaped; the Apply Connect applicant paths are under partner NDA —
verify them once you have access. Mock mode does not touch any of them.
"""

from __future__ import annotations

import os

import httpx

API_BASE = "https://api.linkedin.com/rest"
# Versioned /rest endpoints reject versions older than ~12 months (426
# NONEXISTENT_VERSION). Keep this current; /v2/userinfo is unversioned.
LINKEDIN_VERSION = os.getenv("LINKEDIN_API_VERSION", "202605")


def use_mock() -> bool:
    return os.getenv("USE_MOCK_LINKEDIN", "true").lower() == "true"


def _headers() -> dict:
    from token_manager import get_valid_token

    return {
        "Authorization": f"Bearer {get_valid_token()}",
        "LinkedIn-Version": LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def _company_urn() -> str:
    urn = os.getenv("LINKEDIN_COMPANY_URN")
    if not urn:
        raise RuntimeError("LINKEDIN_COMPANY_URN not set (urn:li:organization:<id>).")
    return urn


# ── Mock data ─────────────────────────────────────────────────────────
_MOCK_JOBS = [
    {
        "id": "3901234567",
        "title": "Senior Backend Engineer",
        "formattedLocation": "Remote, India",
        "jobPostingState": "LISTED",
        "description": "Build the API platform powering our autonomous fleet. "
        "5+ yrs Python, FastAPI/Django, PostgreSQL, Redis, Celery, Docker, AWS.",
        "skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Celery", "Docker", "AWS"],
    }
]

# Inbound applicants — i.e. people who clicked Apply on the posting above.
_MOCK_APPLICANTS = {
    "3901234567": [
        {
            "applicationId": "appl-1001",
            "firstName": "Priya",
            "lastName": "Sharma",
            "email": "priya.sharma@example.com",
            "headline": "Senior Backend Engineer | Python, FastAPI, Postgres",
            "linkedinProfile": "https://linkedin.com/in/priya-sharma-mock",
            "location": "Bengaluru, India",
            "skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "AWS", "Celery"],
            "experienceYears": 7,
            "resumeUrl": "https://example.com/resumes/priya.pdf",
            "screeningAnswers": [{"q": "Years with Python?", "a": "7"}],
        },
        {
            "applicationId": "appl-1002",
            "firstName": "Marcus",
            "lastName": "Lee",
            "email": "marcus.lee@example.com",
            "headline": "Backend Developer | Django, Python, Kafka",
            "linkedinProfile": "https://linkedin.com/in/marcus-lee-mock",
            "location": "Toronto, Canada",
            "skills": ["Python", "Django", "Kafka", "PostgreSQL", "Docker"],
            "experienceYears": 4,
            "resumeUrl": "https://example.com/resumes/marcus.pdf",
            "screeningAnswers": [{"q": "Years with Python?", "a": "4"}],
        },
        {
            "applicationId": "appl-1003",
            "firstName": "Dana",
            "lastName": "Whitfield",
            "email": "dana.w@example.com",
            "headline": "Full-Stack Engineer | Node.js, React, some Python",
            "linkedinProfile": "https://linkedin.com/in/dana-whitfield-mock",
            "location": "London, UK",
            "skills": ["JavaScript", "Node.js", "React", "Python", "MongoDB"],
            "experienceYears": 3,
            "resumeUrl": "https://example.com/resumes/dana.pdf",
            "screeningAnswers": [{"q": "Years with Python?", "a": "1"}],
        },
        {
            "applicationId": "appl-1004",
            "firstName": "Arjun",
            "lastName": "Mehta",
            "email": "arjun.mehta@example.com",
            "headline": "Staff Engineer | Python, FastAPI, Kubernetes, pgvector",
            "linkedinProfile": "https://linkedin.com/in/arjun-mehta-mock",
            "location": "Pune, India",
            "skills": ["Python", "FastAPI", "PostgreSQL", "pgvector", "Kubernetes", "AWS", "Redis"],
            "experienceYears": 9,
            "resumeUrl": "https://example.com/resumes/arjun.pdf",
            "screeningAnswers": [{"q": "Years with Python?", "a": "9"}],
        },
    ]
}


# ── Public API ────────────────────────────────────────────────────────
def verify_live_token() -> dict:
    """Hit LinkedIn's OpenID Connect /userinfo with the configured token.

    Always live (ignores USE_MOCK_LINKEDIN) — this is how you confirm a real
    token works before partner approval. Requires `openid profile email` scopes.
    Falls back to the legacy /v2/me if /userinfo is not authorized for the token.
    """
    import os

    from token_manager import get_valid_token

    headers = {
        "Authorization": f"Bearer {get_valid_token()}",
        "LinkedIn-Version": LINKEDIN_VERSION,
    }
    r = httpx.get("https://api.linkedin.com/v2/userinfo", headers=headers, timeout=20)
    if r.status_code == 200:
        return {"endpoint": "/v2/userinfo", **r.json()}

    # Fall back to the legacy lite-profile endpoint for older scope sets.
    r2 = httpx.get(
        "https://api.linkedin.com/v2/me",
        headers={**headers, "X-Restli-Protocol-Version": "2.0.0"},
        timeout=20,
    )
    if r2.status_code == 200:
        return {"endpoint": "/v2/me", **r2.json()}

    raise RuntimeError(
        f"/userinfo → {r.status_code} {r.text[:160]} | /v2/me → {r2.status_code} {r2.text[:160]}"
    )


def me() -> dict:
    """Identity lookup (mock-aware) used by general callers."""
    if use_mock():
        return {"sub": "mock-member", "name": "Test TA Developer"}
    return verify_live_token()


def get_company_jobs(state: str = "LISTED") -> list[dict]:
    if use_mock():
        return [j for j in _MOCK_JOBS if j["jobPostingState"] == state]
    # CONFIRM against Job Posting API docs after approval.
    resp = httpx.get(
        f"{API_BASE}/simpleJobPostings",
        headers=_headers(),
        params={
            "q": "criteria",
            "postingPublisherUrn": _company_urn(),
            "jobPostingState": state,
            "count": 50,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("elements", [])


def get_job_applicants(job_id: str) -> list[dict]:
    """List applicants who applied to one of your postings (Apply Connect)."""
    if use_mock():
        return _MOCK_APPLICANTS.get(job_id, [])
    # CONFIRM: Apply Connect delivers applications via webhook events and/or a
    # jobApplications read endpoint under the partner program. Wire the real
    # path here once you have the partner docs.
    resp = httpx.get(
        f"{API_BASE}/jobApplications",
        headers=_headers(),
        params={"q": "job", "job": f"urn:li:simpleJobPosting:{job_id}", "count": 100},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("elements", [])


def create_test_job(title: str, description: str, location: str, remote: bool = False) -> dict:
    if use_mock():
        return {"status": "mock", "title": title, "note": "USE_MOCK_LINKEDIN=true"}
    # CONFIRM against Job Posting API docs after approval.
    from datetime import datetime, timezone

    payload = {
        "externalJobPostingId": f"test-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "title": title,
        "description": {"text": description},
        "employmentStatus": "FULL_TIME",
        "workplaceTypes": ["REMOTE"] if remote else ["ON_SITE"],
        "location": location,
        "jobPostingPublisherUrn": _company_urn(),
    }
    resp = httpx.post(
        f"{API_BASE}/simpleJobPostingTasks", headers=_headers(), json=payload, timeout=30
    )
    resp.raise_for_status()
    return {"status": "created", "title": title}
