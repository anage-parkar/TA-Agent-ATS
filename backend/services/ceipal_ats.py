"""Ceipal ATS API (Option B) — pull applicants who applied via the careers portal.

Confirmed-real endpoints (return 400 'credentials mandatory' without auth):
  POST /v1/createAuthtoken/    {email, password, api_key}      -> access token
  GET  /v1/getApplicantsList/  (Bearer)                        -> applicants
  GET  /v1/getApplicantDetails/(Bearer, applicant_id)          -> full profile

Gated by CEIPAL_ATS_EMAIL / _PASSWORD / _API_KEY (admin creds, separate from the
public widget key). When unset, availability() reports what's missing and the
sync endpoint returns a friendly message instead of failing.

⚠️ The response field names + the exact 'applicants who applied to a posting'
filter are marked CONFIRM — finalised against a live response once creds exist.
"""

from __future__ import annotations

import logging

import httpx

from services.config import settings

logger = logging.getLogger("ta_agent.ceipal_ats")

ATS_BASE = "https://api.ceipal.com/v1"
_token: str | None = None


def availability() -> tuple[bool, str]:
    if not (settings.ceipal_ats_email and settings.ceipal_ats_password and settings.ceipal_ats_api_key):
        return False, (
            "Ceipal ATS pull is not configured. Set CEIPAL_ATS_EMAIL, "
            "CEIPAL_ATS_PASSWORD and CEIPAL_ATS_API_KEY (from your Ceipal admin → API)."
        )
    return True, "ready"


def _get_token(force: bool = False) -> str:
    global _token
    if _token and not force:
        return _token
    resp = httpx.post(
        f"{ATS_BASE}/createAuthtoken/",
        json={
            "email": settings.ceipal_ats_email,
            "password": settings.ceipal_ats_password,
            "api_key": settings.ceipal_ats_api_key,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token") or data.get("token") or data.get("accessToken")
    if not token:
        raise RuntimeError(f"Ceipal auth returned no token: {str(data)[:200]}")
    _token = token
    return token


def _headers() -> dict:
    return {"Authorization": f"Bearer {_get_token()}", "Content-Type": "application/json"}


def list_applicants(params: dict | None = None) -> list[dict]:
    """List applicants from the ATS. CONFIRM the params (job posting / date
    filters) + the results key against a live response."""
    resp = httpx.get(f"{ATS_BASE}/getApplicantsList/", headers=_headers(), params=params or {}, timeout=40)
    if resp.status_code == 401:  # token expired → refresh once
        _get_token(force=True)
        resp = httpx.get(f"{ATS_BASE}/getApplicantsList/", headers=_headers(), params=params or {}, timeout=40)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results") or data.get("applicants") or (data if isinstance(data, list) else [])


def get_applicant_details(applicant_id: str) -> dict:
    resp = httpx.get(
        f"{ATS_BASE}/getApplicantDetails/",
        headers=_headers(),
        params={"applicant_id": applicant_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def to_submission(applicant: dict) -> dict:
    """Map a Ceipal applicant record to our ApplicantSubmission fields.
    CONFIRM field names against a live applicant payload."""
    first = applicant.get("first_name") or applicant.get("firstname") or ""
    last = applicant.get("last_name") or applicant.get("lastname") or ""
    return {
        "full_name": f"{first} {last}".strip() or applicant.get("name") or "Applicant",
        "email": applicant.get("email") or applicant.get("email_address"),
        "phone": applicant.get("mobile_number") or applicant.get("phone"),
        "linkedin_url": applicant.get("linkedin") or applicant.get("linkedin_url"),
        "headline": applicant.get("job_title") or applicant.get("designation"),
        "location": applicant.get("city") or applicant.get("location"),
        "skills": applicant.get("skills") or [],
        "experience_years": applicant.get("total_experience"),
        "ceipal_applicant_id": applicant.get("id") or applicant.get("applicant_id"),
    }
