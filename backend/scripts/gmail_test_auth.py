"""Mint the refresh token for the DEDICATED TEST Gmail mailbox.

The test mailbox is used only by scripts/verify_gmail_message_id.py (the
Phase 2 / PR 3 precondition check). Run this yourself, in a normal terminal,
from backend/ — the token is written to disk and never printed, so it never
enters a chat session:

    python scripts/gmail_test_auth.py

Reads GMAIL_TEST_CLIENT_ID and GMAIL_TEST_CLIENT_SECRET (and, if present,
GMAIL_TEST_ADDRESS) from backend/secrets/gmail-test.env — environment
variables of the same names work as a fallback — opens the browser consent
flow for the two Gmail scopes the app itself uses, and writes the resulting
refresh token back into that file, creating or updating ONLY the
GMAIL_TEST_REFRESH_TOKEN line. Output is a confirmation and the file path.

Safety: if GMAIL_TEST_ADDRESS is set, the mailbox you consented with must be
that address, otherwise nothing is written — this is what stops a token for
the live recruiting mailbox from ever landing in the test file.

Refresh tokens for an OAuth app in "Testing" status expire after 7 days;
re-run this when that happens (an Internal/Workspace app has no expiry).
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _envfile import read_env_file, upsert_env_value  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
ENV_PATH = pathlib.Path(__file__).resolve().parents[1] / "secrets" / "gmail-test.env"


def _setting(cfg: dict[str, str], key: str) -> str:
    return (cfg.get(key) or os.environ.get(key) or "").strip()


def main() -> int:
    cfg = read_env_file(ENV_PATH)
    client_id = _setting(cfg, "GMAIL_TEST_CLIENT_ID")
    client_secret = _setting(cfg, "GMAIL_TEST_CLIENT_SECRET")
    expected = _setting(cfg, "GMAIL_TEST_ADDRESS")
    if not client_id or not client_secret:
        print(
            f"Missing GMAIL_TEST_CLIENT_ID / GMAIL_TEST_CLIENT_SECRET.\n"
            f"Put them in {ENV_PATH} (a Desktop-app OAuth client of a project with the Gmail API "
            f"enabled; the test mailbox added as a test user), optionally with GMAIL_TEST_ADDRESS, then re-run."
        )
        return 1

    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=SCOPES,
    )
    # offline => a refresh token is issued; consent => issued even if this
    # account consented before (Google only returns it on the first consent).
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        print("Google returned no refresh token. Re-run; if it persists, revoke the app's access in "
              "the Google account's security settings and consent again.")
        return 1

    # Which mailbox did we just authorise? Refuse to save a token for the wrong one.
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    address = (service.users().getProfile(userId="me").execute().get("emailAddress") or "").strip()
    if expected and address.lower() != expected.lower():
        print(f"Refusing to save: you consented as {address}, but GMAIL_TEST_ADDRESS is {expected}. "
              f"Nothing was written.")
        return 1

    upsert_env_value(ENV_PATH, "GMAIL_TEST_REFRESH_TOKEN", creds.refresh_token)
    if not expected:
        upsert_env_value(ENV_PATH, "GMAIL_TEST_ADDRESS", address)
    print(f"Saved GMAIL_TEST_REFRESH_TOKEN for {address}")
    print(f"-> {ENV_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
