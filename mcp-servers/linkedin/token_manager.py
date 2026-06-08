"""Manages LinkedIn OAuth 2.0 token refresh.

LinkedIn access tokens last ~60 days; refresh tokens last ~365 days (refresh
tokens are only issued to approved partner apps — basic apps must re-auth).

Notes / corrections vs. common outdated guides:
- Member auth uses **OpenID Connect** scopes now: `openid profile email`
  (the old `r_liteprofile` / `r_emailaddress` are deprecated).
- Job Posting + Apply Connect scopes are partner-granted and only appear on
  your app once LinkedIn approves the Talent Solutions products.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv, set_key

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_token_expiring_soon(hours_buffer: int = 24) -> bool:
    """True if the access token expires within `hours_buffer` hours (or is unset)."""
    expiry_str = os.getenv("LINKEDIN_TOKEN_EXPIRY", "")
    if not expiry_str:
        return True
    try:
        expiry = datetime.fromisoformat(expiry_str)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return _utcnow() >= expiry - timedelta(hours=hours_buffer)
    except ValueError:
        return True


def refresh_access_token() -> str:
    """Exchange the refresh token for a new access token; persist to .env.

    Raises RuntimeError if no refresh token is configured (basic apps) or the
    exchange fails.
    """
    client_id = os.getenv("LINKEDIN_CLIENT_ID")
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET")
    refresh_token = os.getenv("LINKEDIN_REFRESH_TOKEN")

    if not refresh_token:
        raise RuntimeError(
            "No LINKEDIN_REFRESH_TOKEN set. Refresh tokens are only issued to "
            "approved partner apps; otherwise re-run the 3-legged OAuth flow and "
            "paste a fresh LINKEDIN_ACCESS_TOKEN."
        )

    resp = httpx.post(
        LINKEDIN_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh failed {resp.status_code}: {resp.text}")

    data = resp.json()
    new_token = data["access_token"]
    expires_in = data.get("expires_in", 5_183_944)  # ~60 days
    new_expiry = (_utcnow() + timedelta(seconds=expires_in)).isoformat()

    set_key(str(ENV_FILE), "LINKEDIN_ACCESS_TOKEN", new_token)
    set_key(str(ENV_FILE), "LINKEDIN_TOKEN_EXPIRY", new_expiry)
    if "refresh_token" in data:
        set_key(str(ENV_FILE), "LINKEDIN_REFRESH_TOKEN", data["refresh_token"])
    os.environ["LINKEDIN_ACCESS_TOKEN"] = new_token
    os.environ["LINKEDIN_TOKEN_EXPIRY"] = new_expiry
    return new_token


def get_valid_token() -> str:
    """Return a valid access token, refreshing if it's close to expiry."""
    if is_token_expiring_soon():
        return refresh_access_token()
    token = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("No LINKEDIN_ACCESS_TOKEN set. Run the OAuth flow first.")
    return token
