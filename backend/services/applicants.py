"""Shared inbound-applicant ingestion.

Both ingestion paths funnel through `ingest_applicant`:
  - the offsite Apply form  (source="offsite_form")
  - the Apply Connect webhook (source="linkedin_apply_connect")

Each applicant becomes a candidate + an application(status="applied"), ready
for the ATS scoring agent (POST /api/jobs/{job_id}/score-applicants).
"""

from __future__ import annotations

import json
import logging

from db import repository
from models.candidate import ApplicantSubmission

logger = logging.getLogger("ta_agent.applicants")


def ingest_applicant(
    job_id: str, applicant: ApplicantSubmission, source: str, status: str = "applied"
) -> dict:
    """Persist one applicant as candidate + application. Idempotent.

    status defaults to "applied" (inbound); Talent Hunt passes "sourced".
    """
    candidate = repository.upsert_candidate(
        {
            "full_name": applicant.full_name,
            "email": applicant.email,
            "phone": applicant.phone,
            "linkedin_url": applicant.linkedin_url,
            "headline": applicant.headline,
            "location": applicant.location,
            "skills": applicant.skills,
            "experience_years": applicant.experience_years,
            "resume_url": applicant.resume_url,
            "raw_profile": json.dumps(applicant.model_dump()),
        }
    )
    app = repository.create_application(
        {
            "job_id": job_id,
            "candidate_id": candidate["id"],
            "status": status,
            "source": source,
        }
    )
    logger.info(
        "Ingested applicant %s for job %s via %s (application %s)",
        applicant.full_name,
        job_id,
        source,
        app["id"],
    )
    return {"application_id": app["id"], "candidate_id": candidate["id"]}
