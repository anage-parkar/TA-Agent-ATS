"""Response parser agent — classifies a candidate's reply to an outreach email.

Used by the reply poller to decide whether to auto-advance the candidate to the
"Replied" pipeline stage (positive/interested) or flag for follow-up.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from services import sanitize
from services.llm import LLMError, get_gateway

logger = logging.getLogger("ta_agent.agents.response_parser")


class ReplyIntent(BaseModel):
    intent: str
    confidence: float = Field(ge=0, le=1)
    summary: str
    follow_up_needed: bool = False


def classify_reply(reply_text: str) -> ReplyIntent:
    """Classify a reply. Raises LLMError on failure/invalid output."""
    # Sanitize the untrusted reply and wrap it in data-only delimiters.
    cleaned = sanitize.sanitize_text(reply_text[:4000]) or ""
    user = sanitize.wrap_untrusted("reply", cleaned)
    try:
        data = get_gateway().complete_json(prompt="response_parser", user=user)
    except LLMError:
        logger.exception("Reply classification failed")
        raise
    return ReplyIntent.model_validate(data)
