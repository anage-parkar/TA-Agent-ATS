"""Candidate models."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CandidateProfile(BaseModel):
    """A sourced candidate profile (from Apify or mock data)."""

    full_name: str
    linkedin_url: Optional[str] = None
    email: Optional[str] = None
    headline: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    experience_years: Optional[int] = None
    location: Optional[str] = None


class SourceRequest(BaseModel):
    job_id: str
    limit: int = 10


class ApplicantSubmission(BaseModel):
    """Normalised inbound applicant — from the offsite form or Apply Connect."""

    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    experience_years: Optional[int] = None
    resume_url: Optional[str] = None
    # Free-form extras (screening answers, cover note, raw event) for the record.
    extra: dict = Field(default_factory=dict)
