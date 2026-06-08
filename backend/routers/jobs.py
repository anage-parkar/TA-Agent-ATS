"""Jobs router — LinkedIn job sync + retrieval."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException

from agents.jd_parser import parse_job
from agents.scoring import score_candidate
from db import repository
from services import linkedin_enrich
from models.candidate import CandidateProfile
from models.job import JobSyncRequest, parsed_from_record
from services import linkedin
from services.config import settings
from services.llm_client import LLMError

logger = logging.getLogger("ta_agent.routers.jobs")

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("/sync")
def sync_job(req: JobSyncRequest):
    """Fetch a LinkedIn job post, parse it, and store the structured result."""
    try:
        raw = linkedin.fetch_job_post(req.linkedin_url)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch job post")
        raise HTTPException(status_code=502, detail=f"LinkedIn fetch failed: {exc}") from exc

    try:
        parsed = parse_job(raw)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    loc = parsed.location
    location_str = (
        "Remote"
        if loc.remote
        else ", ".join(p for p in [loc.city, loc.country] if p) or None
    )

    job = repository.create_job(
        {
            "title": parsed.title,
            "source_url": req.linkedin_url,
            "raw_html": raw,
            "skills": parsed.skills_required,
            "skills_nice_to_have": parsed.skills_nice_to_have,
            "seniority": parsed.seniority,
            "location": location_str,
            "salary_range": json.dumps(
                parsed.salary_range.model_dump() if parsed.salary_range else None
            ),
            "responsibilities": parsed.responsibilities,
            "tech_stack": parsed.tech_stack,
        }
    )

    return {"job_id": job["id"], "parsed_fields": parsed.model_dump()}


@router.post("/ensure")
def ensure_job(payload: dict):
    """Find-or-create a job by typed title; returns its id.

    Used by the Forms/Talent-Hunt panels so the user types a job position
    instead of picking from a dropdown.
    """
    title = (payload or {}).get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Job position is required.")
    job = repository.find_or_create_job_by_title(title)
    return {"job_id": job["id"], "title": job["title"]}


@router.get("")
def list_jobs():
    return {"jobs": repository.list_jobs()}


@router.get("/{job_id}")
def get_job(job_id: str):
    job = repository.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{job_id}/score-applicants")
def score_applicants(job_id: str):
    """Score every not-yet-scored inbound applicant for this job.

    Inbound applicants arrive via the LinkedIn MCP (Apply Connect) as
    applications with status 'applied' and no ats_score. This runs the ATS
    scoring agent over each, fills the score/breakdown, advances status to
    'scored', and returns the ranked list.
    """
    job = repository.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    parsed = parsed_from_record(job)

    apps = repository.list_applications_for_job(job_id)
    pending = [a for a in apps if a.get("ats_score") is None]
    if not pending:
        return {"job_id": job_id, "scored": 0, "candidates": [], "detail": "No unscored applicants."}

    def _score(app):
        cand = app.get("candidate") or {}
        try:
            profile = CandidateProfile.model_validate(cand)
            # Enrich from LinkedIn (cached if already done) and factor it in.
            enrichment = linkedin_enrich.get_or_create_enrichment(cand)
            breakdown = score_candidate(parsed, profile, enrichment=enrichment)
            return app, profile, breakdown
        except (LLMError, Exception) as exc:  # noqa: BLE001
            logger.error("Scoring failed for application %s: %s", app.get("id"), exc)
            return app, None, None

    with ThreadPoolExecutor(
        max_workers=min(settings.llm_max_concurrency, len(pending))
    ) as pool:
        scored = list(pool.map(_score, pending))

    results = []
    for app, profile, breakdown in scored:
        if breakdown is None:
            continue
        repository.update_application(
            app["id"],
            {
                "ats_score": round(breakdown.overall_score, 2),
                "ats_breakdown": json.dumps(breakdown.model_dump()),
                "status": "scored",
            },
        )
        results.append(
            {
                "application_id": app["id"],
                "candidate": profile.model_dump(),
                "ats_score": breakdown.overall_score,
                "ats_breakdown": breakdown.model_dump(),
            }
        )

    results.sort(key=lambda r: r["ats_score"], reverse=True)
    return {"job_id": job_id, "scored": len(results), "candidates": results}
