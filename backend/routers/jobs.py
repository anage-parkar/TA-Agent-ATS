"""Jobs router — LinkedIn job sync + retrieval."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Body, Depends, HTTPException

from services.rbac import require_permission
from agents.jd_parser import parse_job
from agents.scoring import score_candidate_full
from db import repository
from services import linkedin_enrich
from models.candidate import CandidateProfile
from models.job import JobSyncRequest, parsed_from_record
from services import events, linkedin
from services.config import settings
from services.llm_client import LLMError
from services.tenant_context import get_principal, use_principal

logger = logging.getLogger("ta_agent.routers.jobs")

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("/sync", dependencies=[Depends(require_permission("job.sync"))])
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


@router.post("/ensure", dependencies=[Depends(require_permission("job.create"))])
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


@router.post("/{job_id}/score-applicants", dependencies=[Depends(require_permission("application.score"))])
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
    # Per-job rubric config (Workstream D) — falls back to defaults in the agent.
    weights = job.get("weights")
    threshold = job.get("threshold")
    requirements = job.get("requirements")

    apps = repository.list_applications_for_job(job_id)
    pending = [a for a in apps if a.get("ats_score") is None]
    if not pending:
        return {"job_id": job_id, "scored": 0, "candidates": [], "detail": "No unscored applicants."}

    # Worker threads don't inherit the request contextvar, so capture the
    # principal here and rebind it inside each worker (keeps tenant scoping +
    # usage attribution correct for the gateway call).
    principal = get_principal()

    def _score(app):
        cand = app.get("candidate") or {}
        try:
            with use_principal(principal):
                profile = CandidateProfile.model_validate(cand)
                # Enrich from LinkedIn (cached if already done) and factor it in.
                enrichment = linkedin_enrich.get_or_create_enrichment(cand)
                res = score_candidate_full(
                    parsed, profile, enrichment=enrichment,
                    weights=weights, threshold=threshold, requirements=requirements,
                )
            return app, profile, res
        except (LLMError, Exception) as exc:  # noqa: BLE001
            logger.error("Scoring failed for application %s: %s", app.get("id"), exc)
            return app, None, None

    with ThreadPoolExecutor(
        max_workers=min(settings.llm_max_concurrency, len(pending))
    ) as pool:
        scored = list(pool.map(_score, pending))

    results = []
    for app, profile, res in scored:
        if res is None:
            continue
        breakdown = res.breakdown
        repository.update_application(
            app["id"],
            {
                "ats_score": round(breakdown.overall_score, 2),
                "ats_breakdown": json.dumps(breakdown.model_dump()),
                "status": "scored",
            },
        )

        if not res.passed_knockouts:
            # Deterministic knockout — NOT an AI decision; record an audit event.
            events.emit_event(
                "candidate.knocked_out",
                entity_type="application", entity_id=app["id"],
                actor_type="system", actor_id="knockouts",
                metadata={"reasons": res.knockout_reasons},
            )
        else:
            meta = res.llm_result
            # Explainable AI decision: per-dimension scores + cited evidence + versions.
            repository.create_ai_decision(
                {
                    "application_id": app["id"],
                    "candidate_id": app.get("candidate_id"),
                    "job_id": job_id,
                    "kind": "ats_score",
                    "model_version": meta.model_version,
                    "prompt_name": meta.prompt_name,
                    "prompt_version": meta.prompt_version,
                    "prompt_hash": meta.prompt_hash,
                    "input_hash": meta.input_hash,
                    "scores": {**breakdown.model_dump(), "weights": res.weights,
                               "threshold": res.threshold},
                    "evidence": res.evidence,
                }
            )
            events.emit_event(
                "candidate.scored",
                entity_type="application", entity_id=app["id"],
                actor_type="ai", actor_id="scoring.rubric",
                metadata={"overall_score": breakdown.overall_score,
                          "below_threshold": res.below_threshold},
            )

        results.append(
            {
                "application_id": app["id"],
                "candidate": profile.model_dump(),
                "ats_score": breakdown.overall_score,
                "ats_breakdown": breakdown.model_dump(),
                "passed_knockouts": res.passed_knockouts,
                "below_threshold": res.below_threshold,
            }
        )

    results.sort(key=lambda r: r["ats_score"], reverse=True)
    return {"job_id": job_id, "scored": len(results), "candidates": results}


@router.get("/{job_id}/review-queue")
def review_queue(job_id: str, threshold: float = 60.0):
    """Human review queue: scored applicants awaiting a human decision.

    AI scoring ranks and surfaces — it never rejects. Sub-threshold candidates
    land here (flagged) for a recruiter to review and act on, instead of being
    auto-rejected. Candidates who asked for human review are flagged too.
    """
    apps = repository.list_applications_for_job(job_id)
    queue = []
    for a in apps:
        if a.get("recruiter_decision"):
            continue  # already decided by a human
        if a.get("ats_score") is None:
            continue  # not scored yet
        score = a.get("ats_score") or 0
        queue.append(
            {
                "application_id": a["id"],
                "candidate": a.get("candidate"),
                "ats_score": score,
                "below_threshold": score < threshold,
                "human_review_requested": bool(a.get("human_review_requested")),
            }
        )
    queue.sort(key=lambda r: r["ats_score"])
    return {
        "job_id": job_id,
        "threshold": threshold,
        "count": len(queue),
        "below_threshold": sum(1 for r in queue if r["below_threshold"]),
        "queue": queue,
    }


@router.post("/{job_id}/bulk-reject", dependencies=[Depends(require_permission("application.decide"))])
def bulk_reject(
    job_id: str,
    application_ids: list[str] | None = Body(default=None, embed=True),
    below_threshold: float | None = Body(default=None, embed=True),
):
    """Reject many applicants in one human click — still a recorded human decision.

    Supply explicit `application_ids`, or `below_threshold` to reject all
    not-yet-decided, scored applicants under that score. Each rejection is
    audited with the acting human (the rejected transition is enforced
    human-only at the repository layer).
    """
    if not application_ids and below_threshold is None:
        raise HTTPException(
            status_code=400, detail="Provide application_ids or below_threshold."
        )

    targets = set(application_ids or [])
    if below_threshold is not None:
        for a in repository.list_applications_for_job(job_id):
            if (
                a.get("recruiter_decision") is None
                and a.get("ats_score") is not None
                and (a.get("ats_score") or 0) < below_threshold
            ):
                targets.add(a["id"])

    rejected = []
    for app_id in targets:
        updated = repository.update_application(
            app_id, {"status": "rejected", "recruiter_decision": "reject"}
        )
        if updated:
            rejected.append(app_id)
    return {"job_id": job_id, "rejected": len(rejected), "application_ids": rejected}
