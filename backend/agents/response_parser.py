"""Response parser agent — classifies a candidate's reply to an outreach email.

Used by the reply poller to decide whether to auto-advance the candidate to the
"Replied" pipeline stage (positive/interested) or flag for follow-up.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from services.llm_client import LLMError, call_claude_json

logger = logging.getLogger("ta_agent.agents.response_parser")

SYSTEM = """Classify this candidate's reply to a recruiter's outreach email.
Return ONLY valid JSON:
{
  "intent": "interested" | "not_interested" | "question" | "neutral",
  "confidence": float 0-1,
  "summary": string (one sentence),
  "follow_up_needed": boolean
}
"interested" = they want to continue / are available / say yes / express enthusiasm.
"not_interested" = they decline / withdraw / not available.
"question" = they ask something before deciding.
"neutral" = auto-reply, out-of-office, or unclear."""


class ReplyIntent(BaseModel):
    intent: str
    confidence: float = Field(ge=0, le=1)
    summary: str
    follow_up_needed: bool = False


def classify_reply(reply_text: str) -> ReplyIntent:
    """Classify a reply. Raises LLMError on failure/invalid output."""
    try:
        data = call_claude_json(SYSTEM, reply_text[:4000], max_tokens=300)
    except LLMError:
        logger.exception("Reply classification failed")
        raise
    return ReplyIntent.model_validate(data)
