"""Talent Hunt — outbound candidate search by skills/role/experience/location.

Data sources (in priority): Apollo MCP + a custom LinkedIn profile scraper.
Both are gated/optional; a criteria-filtered mock pool runs today so results
actually vary with the inputs (not a static 3-person list).

Integration hooks:
  - APOLLO: wire `_search_apollo(criteria)` to Apollo (REST API key, or a
    dedicated `claude -p` agent with the Apollo MCP enabled — note the backend's
    transform calls use --strict-mcp-config, so Apollo needs its own invocation).
  - SCRAPER: wire `_enrich_via_scraper(profiles)` to your custom LinkedIn
    profile scraper to fill skills/experience/contact from the profile URL.
"""

from __future__ import annotations

import logging

from models.candidate import CandidateProfile
from models.sourcing import TalentHuntRequest
from services.config import settings

logger = logging.getLogger("ta_agent.talent_hunt")


class TalentHuntError(RuntimeError):
    """Raised when a configured real source (Apollo) is unavailable.

    We deliberately do NOT fall back to mock when a real source is configured —
    showing fake candidates would be misleading.
    """


# A diverse pool so criteria meaningfully filter/rank the results.
_POOL: list[dict] = [
    {"full_name": "Priya Sharma", "headline": "Senior Backend Engineer | Python, FastAPI",
     "skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "AWS", "Celery"],
     "experience_years": 7, "location": "Bengaluru, India",
     "linkedin_url": "https://linkedin.com/in/priya-sharma-th", "email": "priya.th@example.com"},
    {"full_name": "Arjun Mehta", "headline": "Staff Engineer | Python, Kubernetes, pgvector",
     "skills": ["Python", "FastAPI", "PostgreSQL", "pgvector", "Kubernetes", "AWS", "Redis"],
     "experience_years": 9, "location": "Pune, India",
     "linkedin_url": "https://linkedin.com/in/arjun-mehta-th", "email": "arjun.th@example.com"},
    {"full_name": "Marcus Lee", "headline": "Backend Developer | Django, Kafka",
     "skills": ["Python", "Django", "Kafka", "PostgreSQL", "Docker"],
     "experience_years": 4, "location": "Toronto, Canada",
     "linkedin_url": "https://linkedin.com/in/marcus-lee-th", "email": "marcus.th@example.com"},
    {"full_name": "Sofia Rossi", "headline": "Data Engineer | Python, Spark, Airflow",
     "skills": ["Python", "Spark", "Airflow", "PostgreSQL", "AWS"],
     "experience_years": 6, "location": "Berlin, Germany",
     "linkedin_url": "https://linkedin.com/in/sofia-rossi-th", "email": "sofia.th@example.com"},
    {"full_name": "Daniel Okafor", "headline": "Platform Engineer | Go, Kubernetes, AWS",
     "skills": ["Go", "Kubernetes", "AWS", "Terraform", "Docker"],
     "experience_years": 8, "location": "Remote, UK",
     "linkedin_url": "https://linkedin.com/in/daniel-okafor-th", "email": "daniel.th@example.com"},
    {"full_name": "Hina Patel", "headline": "Full-Stack Engineer | React, Node, Python",
     "skills": ["JavaScript", "React", "Node.js", "Python", "MongoDB"],
     "experience_years": 5, "location": "Ahmedabad, India",
     "linkedin_url": "https://linkedin.com/in/hina-patel-th", "email": "hina.th@example.com"},
    {"full_name": "Chen Wei", "headline": "Senior ML Engineer | Python, PyTorch, FastAPI",
     "skills": ["Python", "PyTorch", "FastAPI", "PostgreSQL", "AWS", "pgvector"],
     "experience_years": 7, "location": "Singapore",
     "linkedin_url": "https://linkedin.com/in/chen-wei-th", "email": "chen.th@example.com"},
    {"full_name": "Olivia Brown", "headline": "Junior Backend Engineer | Python, Flask",
     "skills": ["Python", "Flask", "PostgreSQL", "Docker"],
     "experience_years": 2, "location": "Manchester, UK",
     "linkedin_url": "https://linkedin.com/in/olivia-brown-th", "email": "olivia.th@example.com"},
    {"full_name": "Rahul Verma", "headline": "Backend Lead | Python, FastAPI, Microservices",
     "skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Kafka", "AWS", "Docker"],
     "experience_years": 11, "location": "Hyderabad, India",
     "linkedin_url": "https://linkedin.com/in/rahul-verma-th", "email": "rahul.th@example.com"},
    {"full_name": "Emma Wilson", "headline": "DevOps Engineer | AWS, Terraform, Python",
     "skills": ["AWS", "Terraform", "Python", "Docker", "Kubernetes"],
     "experience_years": 6, "location": "Remote, US",
     "linkedin_url": "https://linkedin.com/in/emma-wilson-th", "email": "emma.th@example.com"},
]


def _match_score(cand: dict, req: TalentHuntRequest) -> float:
    score = 0.0
    want_skills = {s.lower() for s in req.skills}
    have_skills = {s.lower() for s in cand["skills"]}
    if want_skills:
        score += 3.0 * len(want_skills & have_skills) / len(want_skills)
    if req.role and req.role.lower() in cand["headline"].lower():
        score += 2.0
    if req.experience_min is not None and cand["experience_years"] >= req.experience_min:
        score += 1.5
    if req.location:
        loc = req.location.lower()
        if loc in cand["location"].lower() or (
            "remote" in loc and "remote" in cand["location"].lower()
        ):
            score += 1.5
    return score


def _search_mock(req: TalentHuntRequest) -> list[CandidateProfile]:
    # Hard filter on experience if requested, then rank by criteria match.
    pool = [
        c
        for c in _POOL
        if req.experience_min is None or c["experience_years"] >= req.experience_min
    ]
    ranked = sorted(pool, key=lambda c: _match_score(c, req), reverse=True)
    # Keep only candidates with at least some signal when criteria were given.
    if req.skills or req.role:
        ranked = [c for c in ranked if _match_score(c, req) > 0]
    return [CandidateProfile.model_validate(c) for c in ranked[: req.limit]]


def search(req: TalentHuntRequest) -> list[CandidateProfile]:
    """Find candidates matching the criteria. Mock today; Apollo/scraper-ready."""
    # A real source IS configured — use it or surface a clear error. Never
    # silently substitute mock candidates (that's what confused the demo).
    if settings.apollo_api_key:
        import httpx

        try:
            return _search_apollo(req)
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:200]
            if "API_INACCESSIBLE" in body:
                raise TalentHuntError(
                    "Apollo People Search isn't included in your API plan. Upgrade "
                    "Apollo's API access, or remove APOLLO_API_KEY to use the demo "
                    "mock pool. (The claude.ai Apollo connector works interactively "
                    "but the backend can't use it headlessly.)"
                ) from exc
            raise TalentHuntError(
                f"Apollo search failed ({exc.response.status_code}): {body}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise TalentHuntError(f"Apollo search error: {exc}") from exc

    # No real source configured → intentional demo mock pool.
    results = _search_mock(req)
    logger.info(
        "Talent Hunt (mock): role=%s skills=%s exp>=%s loc=%s -> %d",
        req.role, req.skills, req.experience_min, req.location, len(results),
    )
    return results


_APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/search"

# Tech keywords we can recover from an Apollo title string (no structured skills).
_TECH_KEYWORDS = [
    "Python", "FastAPI", "Django", "Flask", "Java", "Spring", "Go", "Node.js",
    "JavaScript", "TypeScript", "React", "PostgreSQL", "MySQL", "MongoDB", "Redis",
    "Kafka", "Celery", "Docker", "Kubernetes", "AWS", "GCP", "Azure", "Terraform",
    "pgvector", "PyTorch", "Spark", "Airflow", "GraphQL", "Microservices",
]


def _skills_from_title(title: str) -> list[str]:
    t = (title or "").lower()
    return [kw for kw in _TECH_KEYWORDS if kw.lower() in t]


def _search_apollo(req: TalentHuntRequest) -> list[CandidateProfile]:
    """Outbound search via Apollo's REST People Search.

    NOTE: search returns masked last names and no emails on most plans —
    reveal requires the People Enrichment endpoint (people/bulk_match), which
    costs credits. We do search-only here (no surprise credit spend); a separate
    reveal step can enrich selected candidates later.
    """
    import httpx

    payload = {
        "person_titles": [req.role] if req.role else [],
        "person_locations": [req.location] if req.location else [],
        "q_keywords": " ".join(req.skills) if req.skills else None,
        "page": 1,
        "per_page": min(req.limit, 25),
    }
    payload = {k: v for k, v in payload.items() if v}

    resp = httpx.post(
        _APOLLO_SEARCH_URL,
        headers={
            "X-Api-Key": settings.apollo_api_key,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    people = resp.json().get("people", [])

    profiles: list[CandidateProfile] = []
    for p in people:
        name = " ".join(x for x in [p.get("first_name"), p.get("last_name")] if x)
        location = ", ".join(
            x for x in [p.get("city"), p.get("state"), p.get("country")] if x
        )
        profiles.append(
            CandidateProfile(
                full_name=name or p.get("name") or "Apollo Candidate",
                linkedin_url=p.get("linkedin_url"),
                email=p.get("email"),
                headline=p.get("title"),
                location=location or None,
                skills=_skills_from_title(p.get("title", "")),
                experience_years=None,
            )
        )
    logger.info("Talent Hunt (Apollo): %d candidate(s)", len(profiles))
    return profiles[: req.limit]
