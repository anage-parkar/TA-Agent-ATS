# LinkedIn profile enrichment (opt-in)

Enrich a candidate with data scraped from their public LinkedIn profile
(about, experience, education, skills) via the `linkedin-scraper` package.

> ⚠️ **Risk.** This drives a real browser **logged into your LinkedIn account**.
> Automated scraping violates LinkedIn's Terms of Service and can get your
> account **rate-limited, restricted, or banned**. It is **off by default** and
> runs only when you click **"Enrich from LinkedIn"** on a candidate. One profile
> at a time, with a delay. Use sparingly and at your own risk.

## Setup (one time)

```bash
cd backend
pip install linkedin-scraper playwright
playwright install chromium
python scripts/create_linkedin_session.py   # opens a browser → log in → saves session.json
```

Then in the repo-root `.env`:
```
LINKEDIN_SCRAPE_ENABLED=true
LINKEDIN_SESSION_FILE=C:\Users\abhishek.nage\TA Agent\backend\secrets\session.json
```
Restart the backend (env changes need a restart).

## Use
Open a candidate (click their card) → in the detail drawer, **Enrich from LinkedIn**.
The scraped data appears under "LinkedIn enrichment" and is stored on the
candidate (`candidates.enrichment`, `enriched_at`).

## Behaviour when not configured
If the package isn't installed, the session is missing, or the flag is off, the
button returns a friendly message (e.g. *"No LinkedIn session found…"*) — it
never crashes the app. `availability()` in `services/linkedin_enrich.py` reports
exactly what's missing.

## Notes
- `backend/secrets/` (where `session.json` lives) is gitignored.
- The session expires periodically; re-run `create_linkedin_session.py` when
  scrapes start failing with auth errors.
- A ToS-safe alternative is Apollo enrichment (your Apollo plan permitting).
