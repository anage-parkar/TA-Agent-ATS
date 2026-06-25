"""ATS scoring pipeline (Workstream D).

Rework of raw-CV scoring into a redacted, configurable, evidence-backed pipeline:

  redact → deterministic knockouts → semantic overlap hint → LLM per-dimension
  scoring with cited evidence → deterministic weighted aggregate (per-job
  weights/threshold).

Key properties:
- The LLM only ever sees the REDACTED profile (no name/contact/location/age).
- Hard requirements and location are decided in code, never by the LLM.
- The overall score is computed from per-job weights — the LLM does not emit it,
  which makes scoring stable and the weighting auditable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from pydantic import ValidationError

from models.application import ATSBreakdown, RubricScores
from models.candidate import CandidateProfile
from models.job import ParsedJob
from services import knockouts, redaction, sanitize, semantic
from services.knockouts import KnockoutResult
from services.llm import LLMError, LLMResult, get_gateway

logger = logging.getLogger("ta_agent.agents.scoring")


class ScoringRejected(LLMError):
    """Raised when model output fails validation (out-of-range/malformed) and
    must NOT be stored as a valid score (Workstream E output guarding)."""


def output_guard(rubric: RubricScores) -> list[str]:
    """Flag anomalous model output for human review (quarantine, don't trust)."""
    issues: list[str] = []
    dims = [rubric.skill_match, rubric.experience_fit, rubric.tech_stack_overlap]
    if all(d.score >= 0.99 for d in dims) and all(
        len((d.evidence or "").strip()) < 15 for d in dims
    ):
        issues.append("all dimensions maxed with no substantive evidence")
    if any(sanitize.detect_injections(d.evidence) for d in dims):
        issues.append("injection directive echoed in model evidence")
    return issues

# The old fixed 40/30/15/15 rubric is now the DEFAULT — overridable per job.
DEFAULT_WEIGHTS: dict[str, float] = {
    "skill_match": 0.40,
    "experience_fit": 0.30,
    "tech_stack_overlap": 0.15,
    "location_match": 0.15,
}
DEFAULT_THRESHOLD = 60.0


@dataclass
class ScoreResult:
    breakdown: ATSBreakdown
    passed_knockouts: bool
    knockout_reasons: list[str]
    weights: dict[str, float]
    threshold: float
    below_threshold: bool
    evidence: dict = field(default_factory=dict)
    llm_result: Optional[LLMResult] = None     # None when knocked out (no LLM call)
    redacted_profile: dict = field(default_factory=dict)
    quarantined: bool = False                  # anomalous output flagged for human review
    anomalies: list[str] = field(default_factory=list)


def _enrichment_summary(enrichment: dict) -> dict:
    """Condense LinkedIn enrichment for redaction input."""
    return enrichment  # redaction.redact_profile consumes the raw enrichment shape


def _normalize_weights(weights: dict | None) -> dict[str, float]:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    w = {k: float(v) for k, v in w.items() if k in DEFAULT_WEIGHTS}
    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items()}


def _location_match(job: ParsedJob, candidate: dict) -> float:
    """Deterministic location signal in [0,1] — computed in code, not by the LLM."""
    loc = job.location
    if loc.remote:
        return 1.0
    cand_loc = (candidate.get("location") or "").lower()
    if not cand_loc:
        return 0.5  # unknown — neutral (knockouts handle hard location requirements)
    for needle in (loc.city, loc.country):
        if needle and needle.lower() in cand_loc:
            return 1.0
    return 0.0


def score_candidate_full(
    job: ParsedJob,
    candidate: CandidateProfile,
    enrichment: dict | None = None,
    *,
    weights: dict | None = None,
    threshold: float | None = None,
    requirements: dict | None = None,
) -> ScoreResult:
    """Full scoring pipeline. Returns a ScoreResult (breakdown + evidence + meta)."""
    cand = candidate.model_dump()
    weights = _normalize_weights(weights)
    threshold = DEFAULT_THRESHOLD if threshold is None else float(threshold)

    redacted = redaction.redact_profile(cand, enrichment)

    # 1) Deterministic knockouts — outside the LLM, fail-closed.
    ko: KnockoutResult = knockouts.evaluate(requirements, cand)
    if not ko.passed:
        breakdown = ATSBreakdown(
            skill_match=0.0, experience_fit=0.0, location_match=0.0,
            tech_stack_overlap=0.0, overall_score=0.0,
            reasoning="Knocked out: " + "; ".join(ko.failures),
        )
        return ScoreResult(
            breakdown=breakdown, passed_knockouts=False, knockout_reasons=ko.failures,
            weights=weights, threshold=threshold, below_threshold=True,
            evidence={"knockouts": ko.failures}, llm_result=None, redacted_profile=redacted,
        )

    # 2) Semantic skill overlap hint (lexical now; pgvector seam in services.semantic).
    job_terms = list(job.skills_required) + list(job.tech_stack)
    overlap = semantic.semantic_skill_overlap(job_terms, redacted.get("skills") or [])

    # 3) LLM per-dimension scoring on the REDACTED profile only. The candidate
    # data is wrapped in explicit data-only delimiters (defense-in-depth with
    # the sanitization above and the prompt's "treat as data" instruction).
    payload = {"job": job.model_dump(), "semantic_skill_overlap": round(overlap, 3)}
    user = (
        json.dumps(payload, indent=2)
        + "\n\n"
        + sanitize.wrap_untrusted("candidate_profile", json.dumps(redacted, indent=2))
    )
    try:
        result = get_gateway().complete_json(prompt="scoring.rubric", user=user, with_meta=True)
    except LLMError:
        logger.exception("ATS rubric scoring failed")
        raise

    # 3a) Output guarding: reject out-of-range/malformed output (never stored).
    try:
        rubric = RubricScores.model_validate(result.data)
    except ValidationError as exc:
        raise ScoringRejected(f"rubric output failed validation: {exc}") from exc
    anomalies = output_guard(rubric)

    # 4) Deterministic weighted aggregate (per-job weights).
    loc = _location_match(job, cand)
    dims = {
        "skill_match": rubric.skill_match.score,
        "experience_fit": rubric.experience_fit.score,
        "tech_stack_overlap": rubric.tech_stack_overlap.score,
        "location_match": loc,
    }
    overall = round(sum(weights[d] * dims[d] for d in weights) * 100, 2)

    breakdown = ATSBreakdown(
        skill_match=dims["skill_match"],
        experience_fit=dims["experience_fit"],
        location_match=loc,
        tech_stack_overlap=dims["tech_stack_overlap"],
        overall_score=overall,
        reasoning=(
            f"skills: {rubric.skill_match.evidence} | "
            f"experience: {rubric.experience_fit.evidence}"
        )[:500],
    )
    evidence = {
        "skill_match": rubric.skill_match.evidence,
        "experience_fit": rubric.experience_fit.evidence,
        "tech_stack_overlap": rubric.tech_stack_overlap.evidence,
        "location_match": f"deterministic={loc}",
        "semantic_skill_overlap": round(overlap, 3),
        "weights": weights,
    }
    if anomalies:
        evidence["anomalies"] = anomalies
    return ScoreResult(
        breakdown=breakdown, passed_knockouts=True, knockout_reasons=[],
        weights=weights, threshold=threshold, below_threshold=overall < threshold,
        evidence=evidence, llm_result=result, redacted_profile=redacted,
        quarantined=bool(anomalies), anomalies=anomalies,
    )


def score_candidate(
    job: ParsedJob, candidate: CandidateProfile, enrichment: dict | None = None
) -> ATSBreakdown:
    """Back-compat wrapper — runs the full pipeline, returns just the breakdown."""
    return score_candidate_full(job, candidate, enrichment).breakdown
