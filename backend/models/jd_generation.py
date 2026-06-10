"""Pydantic models for AI-based Job Description generation."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class JDGenerationRequest(BaseModel):
    business_unit: str
    role: str
    skills: list[str] = Field(min_length=1)
    years_of_experience: int = Field(ge=0)
    designation: str


class JDContent(BaseModel):
    title: str
    summary: str
    responsibilities: list[str]
    required_skills: list[str]
    nice_to_have: list[str]
    qualifications: list[str]
    what_we_offer: list[str]


class GeneratedJD(BaseModel):
    jd_id: str
    business_unit: str
    role: str
    designation: str
    years_of_experience: int
    skills: list[str]
    content: JDContent
    pdf_url: Optional[str] = None
    created_at: str
