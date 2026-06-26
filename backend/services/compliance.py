"""Candidate transparency + vendor posture (Workstream F).

Default AI-use disclosure / privacy text surfaced to candidates, and the
published sub-processor list. Tenants can override the disclosure via
organizations.ai_disclosure / privacy_notice_url.
"""

from __future__ import annotations

DEFAULT_AI_DISCLOSURE = (
    "This employer uses an AI-assisted system to help screen and rank applications. "
    "The AI ranks and surfaces candidates and provides evidence for its assessment; "
    "it does NOT make final rejection decisions — a human reviews outcomes. You can "
    "request human review of any automated assessment of your application at any time."
)

# Published sub-processor list (also mirrored in docs/compliance/subprocessors.md).
SUBPROCESSORS = [
    {"name": "Anthropic", "purpose": "LLM inference (job parsing, scoring, reply classification)", "region": "US"},
    {"name": "Supabase / Postgres", "purpose": "Primary database (tenant data, pgvector)", "region": "configurable"},
    {"name": "Resend", "purpose": "Transactional / outreach email delivery", "region": "US/EU"},
    {"name": "Google (Gmail, Calendar, Forms)", "purpose": "Email, scheduling, form intake", "region": "US/EU"},
    {"name": "Apify", "purpose": "Optional candidate sourcing", "region": "US/EU"},
    {"name": "LinkedIn", "purpose": "Optional job/applicant integration (Apply Connect)", "region": "US/EU"},
]


def disclosure_for(org: dict | None) -> dict:
    org = org or {}
    return {
        "ai_disclosure": org.get("ai_disclosure") or DEFAULT_AI_DISCLOSURE,
        "privacy_notice_url": org.get("privacy_notice_url"),
        "data_region": org.get("region") or "global",
    }
