"""Interview models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

InterviewStage = Literal["Technical", "HR", "Culture", "Final"]
InterviewFormat = Literal["Video", "Phone", "Onsite"]


class InterviewRequest(BaseModel):
    application_id: str
    stage: InterviewStage
    format: InterviewFormat
    scheduled_at: datetime
    duration_minutes: int = 60
    interviewer_email: str


class EmailDraft(BaseModel):
    """Shared schema for outreach + interview-invite agent output."""

    subject: str
    body: str


class InterviewRecord(BaseModel):
    id: str
    application_id: str
    stage: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    duration_minutes: int = 60
    format: Optional[str] = None
    interviewer_email: Optional[str] = None
    calendar_event_id: Optional[str] = None
    confirmation_status: str = "pending"
