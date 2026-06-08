"""Request models for the three sourcing channels."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class TalentHuntRequest(BaseModel):
    """Outbound search criteria (Talent Hunt channel)."""

    role: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    experience_min: Optional[int] = None
    location: Optional[str] = None
    limit: int = 10


class FormsSyncRequest(BaseModel):
    """Pull responses from a Google Form (Forms API) or its linked Sheet."""

    form_id: Optional[str] = None   # Google Form ID; falls back to env default
    sheet_id: Optional[str] = None  # Google Sheet ID (alternative to form_id)
    sheet_range: str = "A1:Z1000"
    tab: Optional[str] = None  # optional worksheet/tab name
