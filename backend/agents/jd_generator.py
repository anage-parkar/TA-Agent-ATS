"""JD Generator agent — creates a professional job description from structured input."""

from __future__ import annotations

import logging

from models.jd_generation import JDContent, JDGenerationRequest
from services.llm import LLMError, get_gateway

logger = logging.getLogger("ta_agent.agents.jd_generator")


def generate_jd(req: JDGenerationRequest) -> JDContent:
    """Generate a complete Job Description from the structured request.

    Raises LLMError on CLI failure or malformed output.
    """
    skills_list = ", ".join(req.skills)
    user_prompt = (
        f"Business Unit: {req.business_unit}\n"
        f"Role: {req.role}\n"
        f"Designation Level: {req.designation}\n"
        f"Years of Experience Required: {req.years_of_experience} year(s)\n"
        f"Required Skills: {skills_list}\n\n"
        f"Generate a professional, detailed Job Description for this role at Parkar Digital. "
        f"Tailor the tone, responsibilities and qualifications appropriately for a "
        f"{req.designation} level position requiring {req.years_of_experience} year(s) of experience."
    )

    try:
        data = get_gateway().complete_json(prompt="jd_generator", user=user_prompt)
    except LLMError:
        logger.exception("JD generator LLM call failed")
        raise

    return JDContent.model_validate(data)
