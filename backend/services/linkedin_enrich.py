"""Opt-in LinkedIn profile enrichment via the `linkedin-scraper` package.

⚠️ This drives a real browser logged into YOUR LinkedIn account (Playwright +
a saved session.json). Automated scraping violates LinkedIn's ToS and can get
your account rate-limited or restricted. It is OFF by default and only runs on
an explicit per-candidate request, one profile at a time.

The actual scrape runs in a SEPARATE process (scripts/scrape_profile.py) — on
Windows the server's event loop can't spawn Playwright's browser subprocess, so
we shell out, exactly like the `claude -p` LLM calls.

Setup:
  1. pip install linkedin-scraper playwright && playwright install chromium
  2. python scripts/create_linkedin_session.py   (logs in once, saves session.json)
  3. .env: LINKEDIN_SCRAPE_ENABLED=true and LINKEDIN_SESSION_FILE=<path>
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from services.config import settings

logger = logging.getLogger("ta_agent.linkedin_enrich")

# One scrape at a time (a browser per scrape is heavy + polite to LinkedIn).
_lock = threading.Lock()
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scrape_profile.py"
_TIMEOUT_S = int(os.getenv("LINKEDIN_SCRAPE_TIMEOUT", "180"))


class EnrichDisabled(RuntimeError):
    """Enrichment isn't configured/available — surfaced to the UI, not an error."""


class EnrichError(RuntimeError):
    """The scrape was attempted but failed (auth/rate-limit/profile)."""


def availability() -> tuple[bool, str]:
    """(enabled, reason). reason explains what's missing when not enabled."""
    if not settings.linkedin_scrape_enabled:
        return False, "LinkedIn enrichment is disabled (set LINKEDIN_SCRAPE_ENABLED=true)."
    if not settings.linkedin_session_file or not os.path.exists(settings.linkedin_session_file):
        return False, (
            "No LinkedIn session found. Run scripts/create_linkedin_session.py and set "
            "LINKEDIN_SESSION_FILE to the saved session.json."
        )
    try:
        import linkedin_scraper  # noqa: F401
    except ImportError:
        return False, (
            "linkedin-scraper not installed. `pip install linkedin-scraper playwright` "
            "and `playwright install chromium`."
        )
    return True, "ready"


def enrich_profile(linkedin_url: str) -> dict:
    """Scrape a LinkedIn profile (in a subprocess) and return an enrichment dict.

    Raises EnrichDisabled if not configured, EnrichError on scrape failure.
    """
    ok, reason = availability()
    if not ok:
        raise EnrichDisabled(reason)
    if not linkedin_url:
        raise EnrichError("Candidate has no LinkedIn URL to enrich.")

    with _lock:  # serialize scrapes
        try:
            proc = subprocess.run(
                [sys.executable, str(_SCRIPT), linkedin_url],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            raise EnrichError(f"LinkedIn scrape timed out after {_TIMEOUT_S}s.") from exc

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()[-400:]
        logger.warning("LinkedIn scrape failed for %s: %s", linkedin_url, err)
        if "auth" in err.lower() or "login" in err.lower() or "session" in err.lower():
            raise EnrichError("LinkedIn session expired — re-run scripts/create_linkedin_session.py.")
        raise EnrichError(f"LinkedIn scrape failed: {err or 'unknown error'}")

    try:
        return json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        logger.error("Could not parse scrape output: %s", (proc.stdout or "")[:400])
        raise EnrichError(f"Could not parse scrape output: {exc}") from exc


def merge_enrichment(old: dict | None, new: dict) -> dict:
    """Merge a fresh scrape into existing enrichment WITHOUT losing data.

    LinkedIn scraping is flaky — a re-scrape can come back partial (e.g. 0
    experiences when throttled). So for list fields keep the richer one, and for
    scalars prefer the new non-empty value. This makes re-enrich purely additive.
    """
    if not old:
        return new
    out = dict(old)
    for key, val in new.items():
        if isinstance(val, list):
            if len(val) >= len(out.get(key) or []):
                out[key] = val
        elif val not in (None, "", [], {}):
            out[key] = val
    return out


def get_or_create_enrichment(candidate: dict) -> dict | None:
    """Return a candidate's LinkedIn enrichment, scraping + caching if needed.

    Used by the scoring flow. Never raises — returns None when disabled, the
    candidate has no LinkedIn URL, or the scrape fails (so scoring proceeds).
    """
    existing = candidate.get("enrichment")
    if existing:
        if isinstance(existing, dict):
            return existing
        try:
            return json.loads(existing)
        except (json.JSONDecodeError, TypeError):
            pass

    ok, _ = availability()
    url = candidate.get("linkedin_url")
    if not ok or not url:
        return None
    try:
        enrichment = enrich_profile(url)
    except Exception as exc:  # noqa: BLE001 — never block scoring
        logger.info("Skipping enrichment for %s: %s", candidate.get("full_name"), exc)
        return None

    cid = candidate.get("id")
    if cid:
        from db import repository  # lazy import to avoid cycles

        repository.update_candidate_enrichment(cid, json.dumps(enrichment))
    return enrichment
