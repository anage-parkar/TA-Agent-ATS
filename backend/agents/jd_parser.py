"""JD parser agent — extracts structured job data from a raw LinkedIn post."""

from __future__ import annotations

import logging

from models.job import ParsedJob
from services.llm_client import LLMError, call_claude_json

logger = logging.getLogger("ta_agent.agents.jd_parser")

SYSTEM = """You are a job description parser. Extract structured data from the LinkedIn job post.
Return ONLY valid JSON — no markdown, no explanation:
{
  "title": string,
  "skills_required": string[],
  "skills_nice_to_have": string[],
  "seniority": "junior"|"mid"|"senior"|"lead"|"director",
  "location": { "city": string, "country": string, "remote": boolean },
  "salary_range": { "min": number, "max": number, "currency": string } | null,
  "responsibilities": string[],
  "tech_stack": string[]
}"""


def parse_job(raw_content: str) -> ParsedJob:
    """Parse raw job-post content into a validated ParsedJob.

    Raises LLMError on CLI failure or unparseable/invalid output.
    """
    try:
        data = call_claude_json(SYSTEM, raw_content, max_tokens=1200)
    except LLMError:
        logger.exception("JD parser LLM call failed")
        raise

    # Pydantic validation — guards against malformed agent output
    return ParsedJob.model_validate(data)
