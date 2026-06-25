"""JD parser agent — extracts structured job data from a raw LinkedIn post."""

from __future__ import annotations

import logging

from models.job import ParsedJob
from services.llm import LLMError, get_gateway

logger = logging.getLogger("ta_agent.agents.jd_parser")


def parse_job(raw_content: str) -> ParsedJob:
    """Parse raw job-post content into a validated ParsedJob.

    Raises LLMError on model failure or unparseable/invalid output.
    """
    try:
        data = get_gateway().complete_json(prompt="jd_parser", user=raw_content)
    except LLMError:
        logger.exception("JD parser LLM call failed")
        raise

    # Pydantic validation — guards against malformed agent output
    return ParsedJob.model_validate(data)
