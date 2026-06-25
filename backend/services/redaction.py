"""Redaction service (Workstream D).

Produces the de-biased `redacted_profile` that the LLM scorer is allowed to see.
Strips identity and affinity signal that has no bearing on job fit but is a
common vector for bias and gaming: name, contact details, photo, age,
graduation/birth years, addresses, gendered pronouns, and affinity-group
markers. Location is deliberately removed here because it is handled by the
deterministic knockout/match logic, not the LLM (Engineering Rule 10).

This is heuristic, not a guarantee of perfect anonymisation — it removes the
obvious proxies so scoring runs on skills/experience, and it is the single
place to harden as new proxies are discovered.
"""

from __future__ import annotations

import re

# Gendered / affinity pronouns → neutral.
_PRONOUNS = {
    "he": "they", "she": "they", "him": "them", "her": "them", "his": "their",
    "hers": "theirs", "himself": "themselves", "herself": "themselves",
}

# Affinity-group / protected-characteristic markers to drop from free text.
_AFFINITY_PATTERNS = [
    r"\bwomen\s+in\s+\w+\b", r"\bwoman\b", r"\bwomen\b", r"\bmale\b", r"\bfemale\b",
    r"\bfraternity\b", r"\bsorority\b", r"\bveteran\b", r"\bchurch\b", r"\bmosque\b",
    r"\btemple\b", r"\blgbtq?\+?\b", r"\bdisab\w*\b",
]

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")           # graduation/birth-year proxy
_AFFINITY_RE = re.compile("|".join(_AFFINITY_PATTERNS), re.IGNORECASE)


def _scrub_text(text: str | None, *, name: str | None = None) -> str | None:
    if not text:
        return text
    out = text
    # Remove the person's own name tokens.
    for tok in re.split(r"\s+", (name or "").strip()):
        if len(tok) >= 2:
            out = re.sub(rf"\b{re.escape(tok)}\b", "", out, flags=re.IGNORECASE)
    out = _EMAIL_RE.sub("[redacted]", out)
    out = _PHONE_RE.sub("[redacted]", out)
    out = _YEAR_RE.sub("[year]", out)
    out = _AFFINITY_RE.sub("[redacted]", out)
    # Neutralise pronouns (word-boundary, case-insensitive).
    def _depron(m: re.Match) -> str:
        return _PRONOUNS.get(m.group(0).lower(), m.group(0))
    out = re.sub(r"\b(" + "|".join(_PRONOUNS) + r")\b", _depron, out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip()


def _redact_experience(enrichment: dict, name: str | None) -> list[dict]:
    items = []
    for e in (enrichment.get("experiences") or [])[:10]:
        items.append({
            "role": _scrub_text(e.get("position_title") or e.get("title"), name=name),
            "company": (e.get("institution_name") or e.get("company") or "").split(" · ")[0] or None,
            "duration": e.get("duration"),  # length of tenure is fair signal; absolute dates are not
        })
    return items


def _redact_education(enrichment: dict) -> list[dict]:
    # Degree + field are relevant; institution kept, graduation YEARS dropped.
    return [
        {"degree": e.get("degree"), "field": e.get("field_of_study")}
        for e in (enrichment.get("educations") or [])[:5]
        if e.get("degree")
    ]


def redact_profile(candidate: dict, enrichment: dict | None = None) -> dict:
    """Return the de-biased profile the scorer may see — no PII proxies."""
    name = candidate.get("full_name")
    redacted: dict = {
        "headline": _scrub_text(candidate.get("headline"), name=name),
        "skills": list(candidate.get("skills") or []),
        "experience_years": candidate.get("experience_years"),
        # NOTE: name/email/phone/linkedin_url/location intentionally omitted.
        # location is assessed deterministically (knockouts), not by the LLM.
    }
    if enrichment:
        redacted["about"] = _scrub_text(enrichment.get("about"), name=name)
        redacted["experience"] = _redact_experience(enrichment, name)
        redacted["education"] = _redact_education(enrichment)
        redacted["certifications"] = enrichment.get("certifications")
        extra = enrichment.get("skills") or []
        merged = list(dict.fromkeys([*redacted["skills"], *extra]))  # de-dup, keep order
        redacted["skills"] = merged
    return redacted
