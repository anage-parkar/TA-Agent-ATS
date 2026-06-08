"""Sourcing agent — finds candidate profiles for a job via LinkedIn/Apify."""

from __future__ import annotations

import logging

from models.candidate import CandidateProfile
from services import linkedin

logger = logging.getLogger("ta_agent.agents.sourcing")


def source_for_job(
    *, skills: list[str], location: str | None, seniority: str | None, limit: int = 10
) -> list[CandidateProfile]:
    """Source and normalise candidate profiles for a job's criteria."""
    raw = linkedin.source_candidates(
        skills=skills, location=location, seniority=seniority, limit=limit
    )
    profiles: list[CandidateProfile] = []
    for item in raw:
        try:
            profiles.append(CandidateProfile.model_validate(item))
        except Exception:  # noqa: BLE001 — skip malformed profiles, keep sourcing
            logger.warning("Skipping malformed candidate profile: %s", item)
    logger.info("Sourced %d candidate(s)", len(profiles))
    return profiles
