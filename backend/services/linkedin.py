"""LinkedIn integration via Apify, with built-in mock data for local dev.

When USE_MOCK_SOURCING is true (or no APIFY_API_TOKEN is set), this module
returns realistic mock data instead of calling Apify — so the first vertical
slice runs at zero cost. Set USE_MOCK_SOURCING=false + a real token to scrape.

Scraping etiquette (enforced when calling Apify): min 3s between requests.
"""

from __future__ import annotations

import logging
import time

from services.config import settings

logger = logging.getLogger("ta_agent.linkedin")

_MIN_REQUEST_INTERVAL_S = 3.0
_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < _MIN_REQUEST_INTERVAL_S:
        time.sleep(_MIN_REQUEST_INTERVAL_S - elapsed)
    _last_request_at = time.monotonic()


def _use_mock() -> bool:
    return settings.use_mock_sourcing or not settings.apify_api_token


# ── Mock data ─────────────────────────────────────────────────────────
_MOCK_JOB = """Senior Backend Engineer — Acme Robotics (Remote, US)

About the role:
We're hiring a Senior Backend Engineer to build the API platform powering our
autonomous fleet. You'll own services end-to-end, from design to production.

Requirements:
- 5+ years building backend services in Python
- Strong experience with FastAPI or Django, PostgreSQL, and Redis
- Experience designing event-driven systems (Kafka / Celery)
- Comfortable with Docker and AWS

Nice to have:
- pgvector / embeddings experience
- LangGraph or other agent frameworks

Responsibilities:
- Design and ship REST APIs
- Own data models and migrations
- Mentor mid-level engineers

Compensation: $150,000–$190,000 USD. Fully remote within the US.
"""

_MOCK_CANDIDATES = [
    {
        "full_name": "Priya Sharma",
        "linkedin_url": "https://linkedin.com/in/priya-sharma-mock",
        "email": "priya.sharma@example.com",
        "headline": "Senior Backend Engineer | Python, FastAPI, Postgres",
        "skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "AWS", "Celery"],
        "experience_years": 7,
        "location": "Austin, TX (Remote)",
    },
    {
        "full_name": "Marcus Lee",
        "linkedin_url": "https://linkedin.com/in/marcus-lee-mock",
        "email": "marcus.lee@example.com",
        "headline": "Backend Developer | Django, Python, Kafka",
        "skills": ["Python", "Django", "Kafka", "PostgreSQL", "Docker"],
        "experience_years": 4,
        "location": "Toronto, Canada",
    },
    {
        "full_name": "Dana Whitfield",
        "linkedin_url": "https://linkedin.com/in/dana-whitfield-mock",
        "email": "dana.w@example.com",
        "headline": "Full-Stack Engineer | Node.js, React, some Python",
        "skills": ["JavaScript", "Node.js", "React", "Python", "MongoDB"],
        "experience_years": 3,
        "location": "London, UK",
    },
]


# ── Public API ────────────────────────────────────────────────────────
def fetch_job_post(linkedin_url: str) -> str:
    """Return the raw text/HTML of a LinkedIn job post."""
    if _use_mock():
        logger.info("Using mock job post for %s", linkedin_url)
        return _MOCK_JOB

    _throttle()
    from apify_client import ApifyClient

    client = ApifyClient(settings.apify_api_token)
    run = client.actor("misceres/linkedin-jobs-scraper").call(
        run_input={"urls": [linkedin_url], "count": 1}
    )
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    if not items:
        raise RuntimeError(f"Apify returned no data for {linkedin_url}")
    item = items[0]
    return item.get("descriptionText") or item.get("description") or str(item)


def source_candidates(
    *, skills: list[str], location: str | None, seniority: str | None, limit: int = 10
) -> list[dict]:
    """Return candidate profile dicts matching the given criteria."""
    if _use_mock():
        logger.info("Using mock candidates (skills=%s, location=%s)", skills, location)
        return _MOCK_CANDIDATES[:limit]

    _throttle()
    from apify_client import ApifyClient

    client = ApifyClient(settings.apify_api_token)
    query = " ".join(skills[:5])
    run = client.actor("apimaestro/linkedin-people-search").call(
        run_input={"keywords": query, "location": location or "", "count": limit}
    )
    return list(client.dataset(run["defaultDatasetId"]).iterate_items())
