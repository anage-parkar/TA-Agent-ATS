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
import socket
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from services import email_templates
from services.config import settings

logger = logging.getLogger("ta_agent.email")

_GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


class SendError(RuntimeError):
    """Base for provider send failures."""


class AmbiguousSendError(SendError):
    """The message MAY have been sent (timeout, connection loss, 5xx). The
    ledger marks the attempt `unknown` and resolves it by mailbox lookup."""


class DefiniteSendError(SendError):
    """The message was NOT sent (4xx / bad request / not configured)."""


def classify_send_error(exc: Exception) -> type[SendError]:
    """Ambiguous unless the provider told us definitively that nothing was
    sent. Unknown exception types are ambiguous: we can never assume not-sent."""
    try:
        from googleapiclient.errors import HttpError
    except ImportError:  # pragma: no cover
        HttpError = ()  # type: ignore[assignment]
    if HttpError and isinstance(exc, HttpError):
        status = getattr(getattr(exc, "resp", None), "status", None)
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = None
        return AmbiguousSendError if status is None or status >= 500 else DefiniteSendError
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError, OSError)):
        return AmbiguousSendError
    return AmbiguousSendError


def build_message(to: str, subject: str, body: str, *, decision: str = "proceed",
                  opt_out_url: str | None = None, message_id: str | None = None,
                  idempotency_key: str | None = None) -> MIMEMultipart:
    """The branded HTML message (plain-text fallback), optionally carrying the
    two replay-detection headers the send ledger sets."""
    msg = MIMEMultipart("alternative")
    msg["to"] = to
    msg["from"] = settings.outreach_from_email or "me"
    msg["subject"] = subject
    if message_id:
        msg["Message-ID"] = message_id
    if idempotency_key:
        msg["X-TA-Idempotency-Key"] = idempotency_key
    msg.attach(MIMEText(email_templates.plain_fallback(body, opt_out_url), "plain", "utf-8"))
    msg.attach(MIMEText(email_templates.render_html(body, decision, opt_out_url), "html", "utf-8"))
    return msg


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


def send_email(
    to: str, subject: str, body: str, decision: str = "proceed", opt_out_url: str | None = None,
    *, message_id: str | None = None, idempotency_key: str | None = None,
) -> dict:
    """Send a branded HTML email (with plain-text fallback) from the recruiter's
    Gmail. Returns {thread_id, message_id}. Provider failures are raised as
    AmbiguousSendError / DefiniteSendError (see classify_send_error); an
    unconfigured mailbox is a DefiniteSendError (nothing was sent)."""
    ok, reason = availability()
    if not ok:
        raise DefiniteSendError(reason)

    msg = build_message(to, subject, body, decision=decision, opt_out_url=opt_out_url,
                        message_id=message_id, idempotency_key=idempotency_key)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    try:
        sent = _service().users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as exc:  # noqa: BLE001 — classified, never swallowed
        raise classify_send_error(exc)(f"{type(exc).__name__}: {str(exc)[:300]}") from exc
    return {"thread_id": sent.get("threadId"), "message_id": sent.get("id")}


def find_sent_message(*, message_id: str | None = None, idempotency_key: str | None = None,
                      to: str | None = None, days: int = 7) -> dict | None:
    """Was an attempt actually sent? Looks for the deterministic Message-ID
    first (`rfc822msgid:`), then for the X-TA-Idempotency-Key header on recent
    sent mail to the recipient. Returns {message_id, thread_id, matched_by} or
    None. Raises on mailbox errors (callers must not treat that as 'not sent')."""
    ok, reason = availability()
    if not ok:
        raise RuntimeError(reason)
    svc = _service()
    if message_id:
        res = svc.users().messages().list(
            userId="me", q=f"rfc822msgid:{message_id.strip('<>')}", maxResults=5).execute()
        for m in res.get("messages", []) or []:
            return {"message_id": m["id"], "thread_id": m.get("threadId"), "matched_by": "message_id"}
    if idempotency_key:
        q = f"in:sent newer_than:{days}d" + (f" to:{to}" if to else "")
        res = svc.users().messages().list(userId="me", q=q, maxResults=25).execute()
        for m in res.get("messages", []) or []:
            full = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["X-TA-Idempotency-Key"]).execute()
            headers = {h["name"].lower(): h["value"]
                       for h in (full.get("payload") or {}).get("headers", [])}
            if (headers.get("x-ta-idempotency-key") or "").strip() == idempotency_key:
                return {"message_id": full["id"], "thread_id": full.get("threadId"),
                        "matched_by": "idempotency_header"}
    return None


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
