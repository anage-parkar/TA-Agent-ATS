"""ATS scoring agent — evaluates candidate fit against a job."""

from __future__ import annotations

import json
import logging

from models.application import ATSBreakdown
from models.candidate import CandidateProfile
from models.job import ParsedJob
from services.llm_client import LLMError, call_claude_json

logger = logging.getLogger("ta_agent.agents.scoring")

SYSTEM = """You are an ATS scoring engine. Given a job description and candidate profile,
return ONLY valid JSON:
{
  "skill_match": float 0-1,
  "experience_fit": float 0-1,
  "location_match": float 0-1,
  "tech_stack_overlap": float 0-1,
  "overall_score": float 0-100,
  "reasoning": string (one sentence)
}
If the candidate includes a "linkedin_profile" object (scraped from LinkedIn),
treat it as the authoritative source: use its real experience (roles, companies,
durations), education, certifications and skills to judge experience_fit and
skill_match — it is richer and more reliable than the self-reported fields."""


def _enrichment_summary(enrichment: dict) -> dict:
    """Condense LinkedIn enrichment into the signal the scorer cares about."""
    exps = []
    for e in (enrichment.get("experiences") or [])[:10]:
        exps.append(
            {
                "role": e.get("position_title") or e.get("title"),
                "company": (e.get("institution_name") or e.get("company") or "").split(" · ")[0],
                "duration": e.get("duration"),
                "dates": " - ".join(x for x in [e.get("from_date"), e.get("to_date")] if x),
            }
        )
    edus = [
        {"institution": e.get("institution_name"), "degree": e.get("degree")}
        for e in (enrichment.get("educations") or [])[:5]
        if e.get("degree")
    ]
    return {
        "headline": enrichment.get("headline"),
        "about": enrichment.get("about"),
        "linkedin_skills": enrichment.get("skills") or [],
        "experience": exps,
        "education": edus,
        "certifications": enrichment.get("certifications"),
    }


def score_candidate(
    job: ParsedJob, candidate: CandidateProfile, enrichment: dict | None = None
) -> ATSBreakdown:
    """Score one candidate against one job. Returns a validated ATSBreakdown.

    When `enrichment` (scraped LinkedIn profile) is supplied, the scorer factors
    in the candidate's real experience, education, certifications and skills.

    Raises LLMError on CLI failure or unparseable/invalid output.
    """
    candidate_obj = candidate.model_dump()
    if enrichment:
        candidate_obj["linkedin_profile"] = _enrichment_summary(enrichment)

    user = json.dumps({"job": job.model_dump(), "candidate": candidate_obj}, indent=2)
    try:
        data = call_claude_json(SYSTEM, user, max_tokens=600)
    except LLMError:
        logger.exception("ATS scoring LLM call failed for %s", candidate.full_name)
        raise

    return ATSBreakdown.model_validate(data)
