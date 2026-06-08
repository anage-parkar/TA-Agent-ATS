"""Email send + reply reading via the Gmail API (one OAuth credential does both).

Sending the decision email and reading the candidate's reply both go through the
same Gmail mailbox, so replies are naturally threaded (we match on threadId).

Configure with GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN
(scopes: gmail.send + gmail.readonly) and OUTREACH_FROM_EMAIL. When unset,
availability() reports what's missing and callers degrade to draft-only.
"""

from __future__ import annotations

import base64
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from services import email_templates
from services.config import settings

logger = logging.getLogger("ta_agent.email")

_GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def _real(v: str) -> bool:
    """A setting is configured only if it's non-empty and not a placeholder."""
    v = (v or "").strip()
    return bool(v) and not v.lower().startswith("your-") and "changeme" not in v.lower()


def availability() -> tuple[bool, str]:
    if not (
        _real(settings.gmail_client_id)
        and _real(settings.gmail_client_secret)
        and _real(settings.gmail_refresh_token)
    ):
        return False, (
            "Email is not configured. Set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, "
            "GMAIL_REFRESH_TOKEN (gmail.send + gmail.readonly) and OUTREACH_FROM_EMAIL."
        )
    return True, "ready"


def _service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        None,
        refresh_token=settings.gmail_refresh_token,
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=_GMAIL_SCOPES,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send_email(to: str, subject: str, body: str, decision: str = "proceed") -> dict:
    """Send a branded HTML email (with plain-text fallback) from the recruiter's
    Gmail. Returns {thread_id, message_id}."""
    ok, reason = availability()
    if not ok:
        raise RuntimeError(reason)

    msg = MIMEMultipart("alternative")
    msg["to"] = to
    msg["from"] = settings.outreach_from_email or "me"
    msg["subject"] = subject
    msg.attach(MIMEText(email_templates.plain_fallback(body), "plain", "utf-8"))
    msg.attach(MIMEText(email_templates.render_html(body, decision), "html", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    sent = _service().users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"thread_id": sent.get("threadId"), "message_id": sent.get("id")}


def _plain_text(payload: dict) -> str:
    """Extract plain text from a Gmail message payload (recursing parts)."""
    if payload.get("mimeType", "").startswith("text/plain"):
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _plain_text(part)
        if text:
            return text
    return ""


def list_replies(thread_ids: set[str], days: int = 14) -> list[dict]:
    """Return inbound replies in the given threads (messages we didn't send).

    Each item: {thread_id, from, body, message_id}.
    """
    ok, _ = availability()
    if not ok or not thread_ids:
        return []
    svc = _service()
    from_addr = (settings.outreach_from_email or "").lower()

    out: list[dict] = []
    res = svc.users().messages().list(
        userId="me", q=f"in:inbox newer_than:{days}d", maxResults=100
    ).execute()
    for m in res.get("messages", []):
        full = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
        tid = full.get("threadId")
        if tid not in thread_ids:
            continue
        headers = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
        sender = headers.get("from", "")
        if from_addr and from_addr in sender.lower():
            continue  # our own message in the thread, not a reply
        out.append(
            {
                "thread_id": tid,
                "from": sender,
                "body": _plain_text(full.get("payload", {})) or full.get("snippet", ""),
                "message_id": full.get("id"),
            }
        )
    return out
