"""Create a saved LinkedIn session for the opt-in profile scraper.

Opens a browser, lets you log in to LinkedIn manually, then saves the
authenticated session to session.json so the enrichment scraper can reuse it.

⚠️ This logs in as YOUR LinkedIn account. Automated scraping with it violates
LinkedIn's ToS and risks account restriction. Use sparingly and at your own risk.

Run:
    pip install linkedin-scraper playwright
    playwright install chromium
    python scripts/create_linkedin_session.py
Then set in .env:
    LINKEDIN_SCRAPE_ENABLED=true
    LINKEDIN_SESSION_FILE=C:\\Users\\abhishek.nage\\TA Agent\\backend\\secrets\\session.json
"""

import asyncio
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "secrets" / "session.json"


async def main() -> None:
    from linkedin_scraper import BrowserManager, wait_for_manual_login

    OUT.parent.mkdir(parents=True, exist_ok=True)
    async with BrowserManager(headless=False) as browser:
        await browser.page.goto("https://www.linkedin.com/login")
        print("A browser opened — log in to LinkedIn (you have ~10 minutes).")
        # NOTE: this timeout is in MILLISECONDS. 600000 = 10 minutes.
        await wait_for_manual_login(browser.page, timeout=600_000)
        await browser.save_session(str(OUT))
        print(f"✓ Session saved to {OUT}")
        print(
            "Set LINKEDIN_SESSION_FILE to that path and LINKEDIN_SCRAPE_ENABLED=true in .env."
        )


if __name__ == "__main__":
    asyncio.run(main())
