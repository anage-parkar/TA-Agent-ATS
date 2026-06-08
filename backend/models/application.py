"""Application models, including the ATS scoring agent output schema."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ATSBreakdown(BaseModel):
    """Validated structure returned by the ATS scoring agent."""

    skill_match: float = Field(ge=0, le=1)
    experience_fit: float = Field(ge=0, le=1)
    location_match: float = Field(ge=0, le=1)
    tech_stack_overlap: float = Field(ge=0, le=1)
    overall_score: float = Field(ge=0, le=100)
    reasoning: str


class ApplicationRecord(BaseModel):
    id: str
    job_id: str
    candidate_id: str
    ats_score: Optional[float] = None
    ats_breakdown: Optional[dict] = None
    status: str = "sourced"
    recruiter_decision: Optional[str] = None


class RecruiterDecision(BaseModel):
    decision: str  # "proceed" | "reject"
    send_rejection_email: bool = False
