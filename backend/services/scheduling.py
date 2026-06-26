"""Interview scheduling via Google Calendar (Workstream G).

Proposes free/busy-aware slots and creates a calendar invite. Confirming a slot
is always a HUMAN action (gated at the router). Like email_service, this
degrades gracefully: when Calendar credentials are absent it proposes standard
slots and records the interview without an invite, so the flow still works.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from services.config import settings

logger = logging.getLogger("ta_agent.scheduling")

_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _real(v: str) -> bool:
    v = (v or "").strip()
    return bool(v) and not v.lower().startswith("your-")


def availability() -> tuple[bool, str]:
    if not (
        _real(settings.google_calendar_client_id)
        and _real(settings.google_calendar_client_secret)
        and _real(settings.google_calendar_refresh_token)
    ):
        return False, "Calendar not configured (GOOGLE_CALENDAR_CLIENT_ID/SECRET/REFRESH_TOKEN)."
    return True, "ready"


def _service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        None,
        refresh_token=settings.google_calendar_refresh_token,
        client_id=settings.google_calendar_client_id,
        client_secret=settings.google_calendar_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=_SCOPES,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _candidate_slots(days: int) -> list[datetime]:
    base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    slots: list[datetime] = []
    d, added = 0, 0
    while added < days:
        d += 1
        day = base + timedelta(days=d)
        if day.weekday() >= 5:   # skip weekends
            continue
        slots.extend([day.replace(hour=10), day.replace(hour=14)])  # 10:00 / 14:00 UTC
        added += 1
    return slots


def propose_slots(interviewer_email: str | None = None, days: int = 5, duration_minutes: int = 60) -> dict:
    """Return candidate interview slots, removing the interviewer's busy times when
    Calendar is configured (otherwise standard slots)."""
    ok, reason = availability()
    candidates = _candidate_slots(days)

    busy: list[tuple[datetime, datetime]] = []
    if ok and interviewer_email:
        try:
            svc = _service()
            window_start = candidates[0]
            window_end = candidates[-1] + timedelta(minutes=duration_minutes)
            fb = svc.freebusy().query(body={
                "timeMin": window_start.isoformat(), "timeMax": window_end.isoformat(),
                "items": [{"id": interviewer_email}],
            }).execute()
            for b in fb.get("calendars", {}).get(interviewer_email, {}).get("busy", []):
                busy.append((datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])))
        except Exception:  # noqa: BLE001 — degrade to unfiltered slots
            logger.exception("free/busy query failed; returning unfiltered slots")

    def _free(s: datetime) -> bool:
        e = s + timedelta(minutes=duration_minutes)
        return not any(bs < e and s < be for bs, be in busy)

    slots = [s.isoformat() for s in candidates if _free(s)]
    return {"calendar_configured": ok, "duration_minutes": duration_minutes,
            "slots": slots, "detail": None if ok else reason}


def confirm_slot(*, start_iso: str, duration_minutes: int = 60, summary: str = "Interview",
                 interviewer_email: str | None = None, attendee_email: str | None = None) -> dict:
    """Create the calendar invite (human-confirmed). Degrades to a recorded
    interview without an invite when Calendar isn't configured."""
    ok, reason = availability()
    if not ok:
        return {"calendar_event_id": None, "confirmation_status": "pending", "detail": reason}
    start = datetime.fromisoformat(start_iso)
    end = start + timedelta(minutes=duration_minutes)
    attendees = [{"email": e} for e in (interviewer_email, attendee_email) if e]
    try:
        event = _service().events().insert(
            calendarId="primary",
            sendUpdates="all",
            body={
                "summary": summary,
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
                "attendees": attendees,
            },
        ).execute()
        return {"calendar_event_id": event.get("id"), "confirmation_status": "confirmed", "detail": None}
    except Exception as exc:  # noqa: BLE001
        logger.exception("calendar event creation failed")
        return {"calendar_event_id": None, "confirmation_status": "pending", "detail": str(exc)}
