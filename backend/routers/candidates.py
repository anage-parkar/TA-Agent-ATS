"""Candidates router — sourcing, ATS scoring, recruiter review."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException

from agents import sourcing
from agents.scoring import score_candidate
from db import repository
from models.application import RecruiterDecision
from models.candidate import CandidateProfile, SourceRequest
from models.job import parsed_from_record
from services import linkedin_enrich
from services.config import settings
from services.llm_client import LLMError

logger = logging.getLogger("ta_agent.routers.candidates")

router = APIRouter(prefix="/api/candidates", tags=["candidates"])

_job_to_parsed = parsed_from_record


@router.post("/source")
def source_and_score(req: SourceRequest):
    """Source candidates for a job, score each via the ATS agent, persist apps."""
    job = repository.get_job(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    parsed = _job_to_parsed(job)
    profiles = sourcing.source_for_job(
        skills=parsed.skills_required,
        location=job.get("location"),
        seniority=parsed.seniority,
        limit=req.limit,
    )

    # Persist candidates first (fast, sequential DB writes).
    candidates = [
        repository.upsert_candidate(
            {
                "full_name": p.full_name,
                "linkedin_url": p.linkedin_url,
                "email": p.email,
                "headline": p.headline,
                "skills": p.skills,
                "experience_years": p.experience_years,
                "location": p.location,
                "raw_profile": json.dumps(p.model_dump()),
            }
        )
        for p in profiles
    ]

    # Score concurrently — each call shells out to `claude -p` (~20s cold), and
    # the calls are independent, so a small thread pool turns N sequential
    # scorings into roughly one. Cap workers to stay friendly to the Max session.
    def _score(pair):
        profile, candidate = pair
        try:
            enrichment = linkedin_enrich.get_or_create_enrichment(candidate)
            return score_candidate(parsed, profile, enrichment=enrichment)
        except LLMError as exc:
            logger.error("Skipping scoring for %s: %s", profile.full_name, exc)
            return None

    with ThreadPoolExecutor(
        max_workers=min(settings.llm_max_concurrency, len(profiles) or 1)
    ) as pool:
        breakdowns = list(pool.map(_score, list(zip(profiles, candidates))))

    results = []
    for profile, candidate, breakdown in zip(profiles, candidates, breakdowns):
        if breakdown is None:
            continue
        app = repository.create_application(
            {
                "job_id": req.job_id,
                "candidate_id": candidate["id"],
                "ats_score": round(breakdown.overall_score, 2),
                "ats_breakdown": json.dumps(breakdown.model_dump()),
            }
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
    return {"job_id": req.job_id, "count": len(results), "candidates": results}


@router.get("")
def list_candidates(job_id: str):
    """Ranked applications (candidate + ATS) for a job."""
    apps = repository.list_applications_for_job(job_id)
    for a in apps:
        if isinstance(a.get("ats_breakdown"), str):
            try:
                a["ats_breakdown"] = json.loads(a["ats_breakdown"])
            except (json.JSONDecodeError, TypeError):
                pass
    return {"job_id": job_id, "candidates": apps}


@router.post("/{candidate_id}/enrich")
def enrich_candidate(candidate_id: str):
    """Opt-in LinkedIn enrichment for one candidate (ToS/account-ban risk).

    Returns {ok: false, detail} when not configured (so the UI shows guidance),
    {ok: true, enrichment} on success.
    """
    ok, reason = linkedin_enrich.availability()
    if not ok:
        return {"ok": False, "detail": reason}

    candidate = repository.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    try:
        enrichment = linkedin_enrich.enrich_profile(candidate.get("linkedin_url"))
    except linkedin_enrich.EnrichDisabled as exc:
        return {"ok": False, "detail": str(exc)}
    except linkedin_enrich.EnrichError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Merge into any existing enrichment so a partial re-scrape never loses data.
    existing = candidate.get("enrichment")
    if isinstance(existing, str):
        try:
            existing = json.loads(existing)
        except (json.JSONDecodeError, TypeError):
            existing = None
    merged = linkedin_enrich.merge_enrichment(existing, enrichment)
    repository.update_candidate_enrichment(candidate_id, json.dumps(merged))
    return {"ok": True, "enrichment": merged}


@router.post("/{application_id}/decision")
def recruiter_decision(application_id: str, decision: RecruiterDecision):
    """Human-in-the-loop gate: proceed or reject a candidate."""
    if decision.decision not in ("proceed", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'proceed' or 'reject'")

    status = "approved" if decision.decision == "proceed" else "rejected"
    fields = {"status": status, "recruiter_decision": decision.decision}
    if decision.decision == "proceed":
        fields["stage"] = "Reviewed"  # advance the pipeline stage on proceed
    app = repository.update_application(application_id, fields)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"application_id": application_id, "status": status, "stage": app.get("stage")}
