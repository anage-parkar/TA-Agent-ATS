"""Does a client-set Message-ID survive a Gmail API send? (Phase 2 / PR 3 precondition)

PR 3's idempotent send relies on a deterministic `Message-ID` header for
replay detection. This one-off check sends one message from the DEDICATED
TEST mailbox to itself with a client-set Message-ID and a custom
`X-TA-Idempotency-Key` header, reads the stored copy back, and searches for
it with `rfc822msgid:`. Run from backend/:

    python scripts/verify_gmail_message_id.py

Credentials come ONLY from backend/secrets/gmail-test.env
(GMAIL_TEST_ADDRESS / _CLIENT_ID / _CLIENT_SECRET / _REFRESH_TOKEN, minted by
scripts/gmail_test_auth.py). Hard guards — the script refuses to run unless:

  * sender and recipient are both GMAIL_TEST_ADDRESS;
  * GMAIL_TEST_ADDRESS differs from the live OUTREACH_FROM_EMAIL in the root
    .env, and the test client id differs from the live GMAIL_CLIENT_ID.

It logs header values and the outcome only — never tokens.

Exit codes: 0 = Message-ID preserved and searchable; 2 = Gmail rewrote the
Message-ID (PR 3 must fall back to the custom header); 1 = refused or error.
"""

from __future__ import annotations

import base64
import pathlib
import sys
import time
import uuid
from email.mime.text import MIMEText

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _envfile import read_env_file  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
ENV_PATH = BACKEND / "secrets" / "gmail-test.env"
REQUIRED = ("GMAIL_TEST_ADDRESS", "GMAIL_TEST_CLIENT_ID", "GMAIL_TEST_CLIENT_SECRET", "GMAIL_TEST_REFRESH_TOKEN")


class GuardRefused(RuntimeError):
    """The check would have touched something other than the test mailbox."""


def guard(test: dict[str, str], live_from_email: str, live_client_id: str,
          sender: str, recipient: str) -> None:
    """Refuse anything that is not test-mailbox-to-itself with test-only credentials."""
    address = (test.get("GMAIL_TEST_ADDRESS") or "").strip().lower()
    missing = [k for k in REQUIRED if not (test.get(k) or "").strip()]
    if missing:
        raise GuardRefused(f"missing in {ENV_PATH.name}: {', '.join(missing)}")
    if sender.strip().lower() != address or recipient.strip().lower() != address:
        raise GuardRefused("sender and recipient must both be GMAIL_TEST_ADDRESS")
    if address == (live_from_email or "").strip().lower():
        raise GuardRefused("GMAIL_TEST_ADDRESS equals the live OUTREACH_FROM_EMAIL — use a dedicated test mailbox")
    if (test.get("GMAIL_TEST_CLIENT_ID") or "").strip() == (live_client_id or "").strip():
        raise GuardRefused("GMAIL_TEST_CLIENT_ID equals the live GMAIL_CLIENT_ID — use a separate OAuth client")


def build_message(sender: str, recipient: str, message_id: str, idem_key: str) -> tuple[MIMEText, str]:
    """A tiny self-addressed message carrying the two headers under test.
    Returns the MIME object and the base64url 'raw' body Gmail expects."""
    msg = MIMEText("TA Agent Message-ID survival check. Safe to delete.", "plain")
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = "TA Agent check: client-set Message-ID"
    msg["Message-ID"] = message_id
    msg["X-TA-Idempotency-Key"] = idem_key
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return msg, raw


def _headers(meta: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in (meta.get("payload") or {}).get("headers", [])}


def main() -> int:
    test = read_env_file(ENV_PATH)
    address = (test.get("GMAIL_TEST_ADDRESS") or "").strip()

    # Live values are read only to prove the test mailbox is NOT the live one.
    from services.config import settings

    try:
        guard(test, settings.outreach_from_email, settings.gmail_client_id, address, address)
    except GuardRefused as exc:
        print(f"REFUSED: {exc}")
        return 1

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        None,
        refresh_token=test["GMAIL_TEST_REFRESH_TOKEN"],
        client_id=test["GMAIL_TEST_CLIENT_ID"],
        client_secret=test["GMAIL_TEST_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)

    domain = address.split("@", 1)[1]
    message_id = f"<ta-{uuid.uuid4().hex}@{domain}>"
    idem_key = f"idem:test:{uuid.uuid4().hex}"
    _, raw = build_message(address, address, message_id, idem_key)

    try:
        sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        meta = svc.users().messages().get(
            userId="me", id=sent["id"], format="metadata",
            metadataHeaders=["Message-ID", "X-TA-Idempotency-Key", "From", "To"],
        ).execute()
    except Exception as exc:  # noqa: BLE001 — surface the class and message, nothing else
        print(f"ERROR during send/read: {type(exc).__name__}: {str(exc)[:300]}")
        return 1

    stored = _headers(meta)
    stored_mid = (stored.get("message-id") or "").strip()
    preserved = stored_mid == message_id
    custom_ok = (stored.get("x-ta-idempotency-key") or "").strip() == idem_key

    found: list[str] = []
    for _ in range(6):  # delivery to the same mailbox can lag a few seconds
        res = svc.users().messages().list(userId="me", q=f"rfc822msgid:{message_id.strip('<>')}").execute()
        found = [m["id"] for m in res.get("messages", [])]
        if found:
            break
        time.sleep(5)
    labels = sorted(meta.get("labelIds") or [])

    print(f"sent message id      : {sent['id']} (thread {sent.get('threadId')})")
    print(f"Message-ID sent      : {message_id}")
    print(f"Message-ID stored    : {stored_mid or '(none)'}")
    print(f"Message-ID preserved : {preserved}")
    print(f"X-TA-Idempotency-Key : sent={idem_key} stored={stored.get('x-ta-idempotency-key') or '(none)'} preserved={custom_ok}")
    print(f"rfc822msgid: search  : {len(found)} message(s) {found}")
    print(f"labels on stored copy: {labels}")
    if preserved and found:
        print("OUTCOME: client-set Message-ID survives the API send and is searchable — PR 3 may key replay detection on it.")
        return 0
    if preserved and not found:
        print("OUTCOME: Message-ID preserved but rfc822msgid: search found nothing within 30s — re-run; if persistent, replay lookup must scan recent sent mail by X-TA-Idempotency-Key.")
        return 2
    print("OUTCOME: Gmail REWROTE the Message-ID — PR 3 must fall back to the X-TA-Idempotency-Key header on recent sent mail to the recipient.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
