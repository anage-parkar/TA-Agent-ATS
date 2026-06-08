"""Official website / careers-portal channel.

- Sync active jobs from your careers site into TA Agent.
- Receive portal applications and fan them out to BOTH TA Agent and your
  external partner ATS.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from db import repository
from models.candidate import ApplicantSubmission
from services import ceipal_ats, website
from services.applicants import ingest_applicant

logger = logging.getLogger("ta_agent.routers.website")

router = APIRouter(prefix="/api/website", tags=["website"])


@router.post("/sync-jobs")
def sync_jobs():
    """Pull active jobs from the careers site and upsert them as website jobs."""
    try:
        jobs = website.fetch_active_jobs()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Website job sync failed")
        raise HTTPException(status_code=502, detail=f"Website job sync failed: {exc}") from exc

    synced = []
    for j in jobs:
        rec = repository.create_job(
            {
                "title": j["title"],
                "source_url": f"website:{j['external_id']}",
                "skills": j.get("skills") or [],
                "location": j.get("location"),
                "tech_stack": j.get("skills") or [],
                "raw_html": j.get("description"),
            }
        )
        synced.append({"job_id": rec["id"], "title": rec["title"]})
    return {"count": len(synced), "jobs": synced}


@router.post("/sync-applicants")
def sync_applicants():
    """Option B — pull applicants from Ceipal's ATS API into the Website channel.

    Returns {ok: false, detail} until CEIPAL_ATS_* creds are set, so the UI shows
    guidance instead of failing.
    """
    ok, reason = ceipal_ats.availability()
    if not ok:
        return {"ok": False, "detail": reason}

    try:
        applicants = ceipal_ats.list_applicants()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ceipal applicant pull failed")
        raise HTTPException(status_code=502, detail=f"Ceipal applicant pull failed: {exc}") from exc

    ingested, skipped = 0, 0
    for raw in applicants:
        mapped = ceipal_ats.to_submission(raw)
        # Attach to the matching website job by Ceipal posting id, if present.
        posting_id = raw.get("job_posting_id") or raw.get("job_id") or raw.get("position_id")
        job = repository.get_job_by_source_url(f"website:{posting_id}") if posting_id else None
        if not job:
            skipped += 1
            continue
        mapped.pop("ceipal_applicant_id", None)
        ingest_applicant(job["id"], ApplicantSubmission.model_validate(mapped), source="website_portal")
        ingested += 1

    return {"ok": True, "ingested": ingested, "skipped_no_job_match": skipped, "total": len(applicants)}


@router.post("/jobs/{job_id}/apply")
async def website_apply(
    job_id: str,
    full_name: str = Form(...),
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    linkedin_url: Optional[str] = Form(None),
    headline: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    skills: Optional[str] = Form(None),
    experience_years: Optional[int] = Form(None),
    resume: Optional[UploadFile] = File(None),
):
    """Portal application endpoint — ingests into TA Agent AND fans out to the
    external partner ATS. Your website's apply form posts here."""
    job = repository.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    submission = ApplicantSubmission(
        full_name=full_name,
        email=email,
        phone=phone,
        linkedin_url=linkedin_url,
        headline=headline,
        location=location,
        skills=[s.strip() for s in (skills or "").replace("\n", ",").split(",") if s.strip()],
        experience_years=experience_years,
    )

    # 1) Keep it in TA Agent (this channel)
    result = ingest_applicant(job_id, submission, source="website_portal")

    # 2) Fan-out to the external partner ATS (best-effort, never blocks)
    forward = website.forward_to_partner(
        {"job_id": job_id, "job_title": job.get("title"), **submission.model_dump()}
    )

    return {"ok": True, **result, "partner_forward": forward}
