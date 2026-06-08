"""Scrape one LinkedIn profile and print the enrichment JSON to stdout.

Run as a SEPARATE process (the backend shells out to this) so Playwright gets a
clean event loop that supports subprocesses — on Windows the server's loop does
not, which is why running it in-process raises NotImplementedError.

Usage: python scripts/scrape_profile.py <linkedin_url>
Reads LINKEDIN_SESSION_FILE from the repo-root .env.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Windows: Playwright needs the Proactor loop to spawn the browser subprocess.
# On Windows this is already the default for asyncio.run() (Python 3.8+), so we
# don't set the (now-deprecated) policy explicitly — that just emits warnings.

load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # repo-root .env
SESSION = os.getenv("LINKEDIN_SESSION_FILE", "")


def _dump(obj):
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


async def _scrape(url: str) -> dict:
    from linkedin_scraper import BrowserManager, PersonScraper

    async with BrowserManager(headless=True) as browser:
        await browser.load_session(SESSION)

        # Grab the avatar FIRST — the scraper later navigates to detail sub-pages,
        # so the main-profile top card won't be on the page by the time it returns.
        image_url = None
        try:
            await browser.page.goto(url, wait_until="domcontentloaded")
            await browser.page.wait_for_timeout(2500)
            # og:image meta is the most reliable source of the profile photo.
            try:
                og = browser.page.locator('meta[property="og:image"]').first
                if await og.count() > 0:
                    src = await og.get_attribute("content")
                    if src and "media.licdn.com" in src:
                        image_url = src
            except Exception:  # noqa: BLE001
                pass
            if not image_url:
                for sel in (
                    "img.pv-top-card-profile-picture__image",
                    ".pv-top-card-profile-picture img",
                    'img[src*="media.licdn.com/dms/image"]',
                ):
                    loc = browser.page.locator(sel).first
                    if await loc.count() > 0:
                        src = await loc.get_attribute("src")
                        if src and "data:image" not in src and "media.licdn.com" in src:
                            image_url = src
                            break
        except Exception:  # noqa: BLE001
            pass

        person = await PersonScraper(browser.page).scrape(url)

    accomplishments = getattr(person, "accomplishments", None)
    return {
        "image_url": image_url,
        "name": getattr(person, "name", None),
        "headline": getattr(person, "headline", None),
        "about": getattr(person, "about", None),
        "location": getattr(person, "location", None),
        "skills": list(getattr(person, "skills", []) or []),
        "experiences": [_dump(e) for e in getattr(person, "experiences", []) or []],
        "educations": [_dump(e) for e in getattr(person, "educations", []) or []],
        "certifications": _dump(accomplishments) if accomplishments else None,
        "linkedin_url": getattr(person, "linkedin_url", url),
        "source": "linkedin_scraper",
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: scrape_profile.py <linkedin_url>", file=sys.stderr)
        sys.exit(2)
    try:
        result = asyncio.run(_scrape(sys.argv[1]))
        # Last stdout line is the JSON payload the caller parses.
        print(json.dumps(result, default=str))
    except Exception as exc:  # noqa: BLE001
        print(f"SCRAPE_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
