"""Inbound applications — offsite Apply form + LinkedIn Apply Connect webhook.

Both paths normalise to ApplicantSubmission and call services.applicants.ingest_applicant,
so the downstream pipeline (score → review) is identical regardless of source.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from db import repository
from models.candidate import ApplicantSubmission
from services.applicants import ingest_applicant
from services.config import settings

logger = logging.getLogger("ta_agent.routers.applications")

router = APIRouter(tags=["applications"])

UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads" / "resumes"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_ALLOWED_RESUME_EXT = {".pdf", ".doc", ".docx", ".txt", ".rtf"}


def _split_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in re.split(r"[,\n;]", raw) if s.strip()]


# ── Offsite Apply form ────────────────────────────────────────────────
@router.get("/api/jobs/{job_id}/apply-info")
def apply_info(job_id: str):
    """Public: minimal job info to render the application form."""
    job = repository.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job["id"],
        "title": job.get("title"),
        "location": job.get("location"),
        "skills": job.get("skills") or [],
    }


@router.post("/api/jobs/{job_id}/apply")
async def apply(
    job_id: str,
    full_name: str = Form(...),
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    linkedin_url: Optional[str] = Form(None),
    headline: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    skills: Optional[str] = Form(None),
    experience_years: Optional[int] = Form(None),
    cover_note: Optional[str] = Form(None),
    resume: Optional[UploadFile] = File(None),
):
    """Public application endpoint — the LinkedIn job's Apply button links here."""
    job = repository.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    resume_url = None
    if resume and resume.filename:
        ext = Path(resume.filename).suffix.lower()
        if ext not in _ALLOWED_RESUME_EXT:
            raise HTTPException(status_code=400, detail=f"Unsupported resume type: {ext}")
        fname = f"{uuid.uuid4().hex}{ext}"
        content = await resume.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Resume exceeds 10 MB")
        (UPLOAD_DIR / fname).write_bytes(content)
        resume_url = f"/uploads/resumes/{fname}"

    submission = ApplicantSubmission(
        full_name=full_name,
        email=email,
        phone=phone,
        linkedin_url=linkedin_url,
        headline=headline,
        location=location,
        skills=_split_list(skills),
        experience_years=experience_years,
        resume_url=resume_url,
        extra={"cover_note": cover_note} if cover_note else {},
    )
    result = ingest_applicant(job_id, submission, source="offsite_form")
    return {"ok": True, "message": "Application received.", **result}


# ── LinkedIn Apply Connect webhook (ready for partner approval) ───────
@router.get("/webhooks/linkedin/applications")
def webhook_validate(challengeCode: str | None = None):
    """LinkedIn webhook validation handshake.

    On subscription, LinkedIn GETs this URL with a `challengeCode`; we echo it
    back with an HMAC-SHA256 over the configured client secret.
    ⚠️ CONFIRM the exact handshake fields against the Apply Connect partner docs.
    """
    if not challengeCode:
        return {"status": "ready"}
    secret = (settings.linkedin_webhook_secret or "").encode()
    challenge_response = hmac.new(secret, challengeCode.encode(), hashlib.sha256).hexdigest()
    return {"challengeCode": challengeCode, "challengeResponse": challenge_response}


def _map_apply_connect_event(payload: dict) -> tuple[str | None, ApplicantSubmission]:
    """Map an Apply Connect application event to (job_linkedin_id, ApplicantSubmission).

    ⚠️ CONFIRM field paths against the partner schema — placeholders below mirror
    the documented shape (applicant contact info + resume + screening answers).
    """
    applicant = payload.get("applicant", payload)
    job_urn = payload.get("jobPosting") or payload.get("job") or ""
    job_linkedin_id = job_urn.rsplit(":", 1)[-1] if job_urn else None

    name = (
        applicant.get("name")
        or f"{applicant.get('firstName','')} {applicant.get('lastName','')}".strip()
        or "Unknown Applicant"
    )
    return job_linkedin_id, ApplicantSubmission(
        full_name=name,
        email=applicant.get("email"),
        phone=applicant.get("phoneNumber") or applicant.get("phone"),
        linkedin_url=applicant.get("profileUrl") or applicant.get("linkedinProfile"),
        headline=applicant.get("headline"),
        location=applicant.get("location"),
        skills=applicant.get("skills") or [],
        experience_years=applicant.get("experienceYears"),
        resume_url=applicant.get("resumeUrl"),
        extra={"screeningAnswers": applicant.get("screeningAnswers", []), "raw": payload},
    )


@router.post("/webhooks/linkedin/applications")
async def webhook_receive(request: Request):
    """Receive a LinkedIn Apply Connect application event and ingest it.

    Each event creates a candidate + application(status=applied, source=
    linkedin_apply_connect), feeding the same scoring/review pipeline as the form.
    """
    body = await request.body()

    # Signature verification — LinkedIn signs the payload; verify when a secret
    # is configured. ⚠️ CONFIRM header name / algorithm with the partner docs.
    secret = settings.linkedin_webhook_secret
    if secret:
        sig = request.headers.get("x-li-signature") or request.headers.get("X-LI-Signature")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not sig or not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    job_linkedin_id, submission = _map_apply_connect_event(payload)
    if not job_linkedin_id:
        raise HTTPException(status_code=400, detail="Event missing job posting reference")

    source_url = f"https://www.linkedin.com/jobs/view/{job_linkedin_id}"
    job = repository.get_job_by_source_url(source_url)
    if not job:
        # Don't drop the event; surface it for operator follow-up.
        logger.warning("Apply Connect event for unknown job %s — sync the job first.", job_linkedin_id)
        raise HTTPException(status_code=404, detail=f"Job {job_linkedin_id} not synced yet")

    result = ingest_applicant(job["id"], submission, source="linkedin_apply_connect")
    return {"ok": True, **result}
