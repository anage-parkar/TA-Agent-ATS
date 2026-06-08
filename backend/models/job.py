"""Job models — both the parsed-LLM-output schema and API shapes."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Seniority = Literal["junior", "mid", "senior", "lead", "director"]


class JobLocation(BaseModel):
    city: Optional[str] = None
    country: Optional[str] = None
    remote: bool = False


class SalaryRange(BaseModel):
    min: float
    max: float
    currency: str = "USD"


class ParsedJob(BaseModel):
    """Validated structure returned by the JD parser agent."""

    title: str
    skills_required: list[str] = Field(default_factory=list)
    skills_nice_to_have: list[str] = Field(default_factory=list)
    seniority: Seniority = "mid"
    location: JobLocation = Field(default_factory=JobLocation)
    salary_range: Optional[SalaryRange] = None
    responsibilities: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)


class JobSyncRequest(BaseModel):
    linkedin_url: str


class JobRecord(BaseModel):
    id: str
    title: str
    source_url: Optional[str] = None
    skills: Optional[list[str]] = None
    seniority: Optional[str] = None
    location: Optional[str] = None
    tech_stack: Optional[list[str]] = None


def parsed_from_record(job: dict) -> "ParsedJob":
    """Reconstruct a ParsedJob from a stored job row (for scoring)."""
    return ParsedJob(
        title=job.get("title", ""),
        skills_required=job.get("skills") or [],
        skills_nice_to_have=job.get("skills_nice_to_have") or [],
        seniority=job.get("seniority") or "mid",
        location=JobLocation(remote=(job.get("location") == "Remote")),
        tech_stack=job.get("tech_stack") or [],
        responsibilities=job.get("responsibilities") or [],
    )
