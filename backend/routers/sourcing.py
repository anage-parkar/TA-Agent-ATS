"""Sourcing channels — Talent Hunt (outbound) + Google Forms sync (inbound).

Both ingest into the shared applicant pipeline with a distinct `source`, so the
UI can show them in separate sections. Scoring is done separately via
POST /api/jobs/{job_id}/score-applicants (covers all unscored, any channel).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from db import repository
from models.candidate import ApplicantSubmission
from models.sourcing import FormsSyncRequest, TalentHuntRequest
from services import forms_sync, talent_hunt
from services.applicants import ingest_applicant
from services.config import settings

logger = logging.getLogger("ta_agent.routers.sourcing")

router = APIRouter(prefix="/api/jobs", tags=["sourcing"])


@router.post("/{job_id}/talent-hunt")
def run_talent_hunt(job_id: str, req: TalentHuntRequest):
    """Outbound search by skills/role/experience/location → source=talent_hunt."""
    job = repository.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        profiles = talent_hunt.search(req)
    except talent_hunt.TalentHuntError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ingested = []
    for p in profiles:
        submission = ApplicantSubmission(
            full_name=p.full_name,
            email=p.email,
            linkedin_url=p.linkedin_url,
            headline=p.headline,
            location=p.location,
            skills=p.skills,
            experience_years=p.experience_years,
        )
        res = ingest_applicant(job_id, submission, source="talent_hunt", status="sourced")
        ingested.append({**res, "full_name": p.full_name})

    return {
        "job_id": job_id,
        "channel": "talent_hunt",
        "criteria": req.model_dump(),
        "count": len(ingested),
        "ingested": ingested,
    }


@router.post("/{job_id}/sync-forms")
def sync_forms(job_id: str, req: FormsSyncRequest):
    """Pull responses from THIS job's linked Google Form → source=google_form.

    Form resolution (so one job's form never bleeds into another):
      1. an explicit form link/id in the request → remembered on the job
      2. else the form previously linked to this job
      3. else the optional global GOOGLE_FORM_ID fallback
    """
    job = repository.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Resolve the form id for THIS job (job-scoped, not a single global form).
    try:
        if req.form_id:
            fid = forms_sync.extract_form_id(req.form_id)
            if fid:
                repository.set_job_form_id(job_id, fid)  # remember it for next time
        elif req.sheet_id:
            fid = None  # sheet path handled below
        else:
            fid = job.get("form_id") or forms_sync.extract_form_id(settings.google_form_id)
    except forms_sync.FormsRefError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not fid and not req.sheet_id:
        raise HTTPException(
            status_code=400,
            detail="No form linked to this job yet. Paste the form's EDIT link "
            "(…/forms/d/<id>/edit) in the box and sync — it'll be remembered for this job.",
        )

    try:
        applicants = forms_sync.fetch_form_responses(
            form_id=fid,
            sheet_id=req.sheet_id,
            sheet_range=req.sheet_range,
            tab=req.tab,
        )
    except forms_sync.FormsRefError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Forms sync failed")
        raise HTTPException(status_code=502, detail=f"Forms sync failed: {exc}") from exc

    ingested = []
    for a in applicants:
        res = ingest_applicant(job_id, a, source="google_form", status="applied")
        ingested.append({**res, "full_name": a.full_name})

    return {
        "job_id": job_id,
        "channel": "google_form",
        "count": len(ingested),
        "ingested": ingested,
    }
