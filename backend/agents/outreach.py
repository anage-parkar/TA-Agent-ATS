"""Outreach agent — drafts the decision email sent to a candidate.

Proceed → "you've advanced to the next stage" invite.
Reject  → a warm, respectful decline.

Returns validated {subject, body}. Sending is a separate, human-approved step.
"""

from __future__ import annotations

import json
import logging

from models.interview import EmailDraft
from services.llm_client import LLMError, call_claude_json

logger = logging.getLogger("ta_agent.agents.outreach")

_PROCEED_SYSTEM = """Write a warm, professional email telling a candidate they ADVANCED to the
next hiring stage. Keep it BRIEF — 2 short paragraphs, plain text (no signatures
or footers; the system adds branded header/footer).
- Address them by first name; reference the role.
- Note 1 specific strength from their profile.
- Ask them to reply to confirm interest in moving forward.
- End with a brief sign-off line (e.g. "Best regards, Talent Acquisition Team").
Return ONLY valid JSON: { "subject": string, "body": string }"""

_REJECT_SYSTEM = """Write a respectful, BRIEF rejection email — 2 short paragraphs, plain text
(no footers; the system adds branded header/footer).
- Address them by first name; reference the role.
- Kindly say they weren't selected to move forward; thank them; wish them well.
- No harsh reasons. End with a brief sign-off line.
Return ONLY valid JSON: { "subject": string, "body": string }"""


def template_decision_email(decision: str, candidate: dict, job_title: str) -> EmailDraft:
    """Instant (no-LLM) decision email from a template + light personalisation.

    Used as the default draft so the Proceed/Reject click is immediate. The user
    can then optionally "Rewrite with AI" for a richer version.
    """
    first = (candidate.get("full_name") or "there").split()[0]
    role = job_title or "the role"
    skills = candidate.get("skills") or []
    # The branded HTML footer carries the opt-out + company line — keep the body
    # to just the message + a sign-off.
    if decision == "proceed":
        strength = f" Your experience with {skills[0]} stood out to our team." if skills else ""
        subject = f"You've advanced — {role}"
        body = (
            f"Hi {first},\n\n"
            f"Great news — you've advanced to the next stage of our hiring process for the "
            f"{role} position.{strength}\n\n"
            f"Please reply to this email to confirm your interest and we'll share the next "
            f"steps.\n\nBest regards,\nTalent Acquisition Team"
        )
    else:
        subject = f"Update on your application — {role}"
        body = (
            f"Hi {first},\n\n"
            f"Thank you for your interest in the {role} position and for the time you invested "
            f"in applying. After careful consideration, we won't be moving forward with your "
            f"application at this time.\n\n"
            f"We were impressed by your background and encourage you to apply for future roles "
            f"that match your skills. We wish you all the best.\n\n"
            f"Warm regards,\nTalent Acquisition Team"
        )
    return EmailDraft(subject=subject, body=body)


def draft_decision_email(decision: str, candidate: dict, job_title: str) -> EmailDraft:
    """Draft a proceed/reject email. Raises LLMError on failure/invalid output."""
    system = _PROCEED_SYSTEM if decision == "proceed" else _REJECT_SYSTEM
    user = json.dumps(
        {
            "decision": decision,
            "role_title": job_title,
            "candidate": {
                "full_name": candidate.get("full_name"),
                "headline": candidate.get("headline"),
                "skills": candidate.get("skills"),
                "location": candidate.get("location"),
            },
        },
        indent=2,
    )
    try:
        # Drafting a short email is simple — use the fast model (Haiku) to keep
        # the Proceed/Reject click responsive.
        data = call_claude_json(system, user, max_tokens=500, model="haiku")
    except LLMError:
        logger.exception("Decision email drafting failed")
        raise
    return EmailDraft.model_validate(data)
