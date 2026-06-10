"""JD Generator agent — creates a professional job description from structured input."""

from __future__ import annotations

import logging

from models.jd_generation import JDContent, JDGenerationRequest
from services.llm_client import LLMError, call_claude_json

logger = logging.getLogger("ta_agent.agents.jd_generator")

SYSTEM = """You are an expert Technical Recruiter at Parkar Digital, a global technology consulting \
company specializing in digital transformation, cloud solutions, and enterprise software engineering. \
Parkar Digital partners with Fortune 500 companies to deliver cutting-edge solutions across AI/ML, \
cloud infrastructure, data engineering, and software development.

Given a job role, business unit, selected skills, years of experience, and designation level, \
generate a complete and professional Job Description.

Return ONLY valid JSON — no markdown, no explanation:
{
  "title": "Full descriptive job title including seniority and BU context (e.g. 'Senior Java Developer – Platform Engineering')",
  "summary": "3-4 sentence role overview that describes the opportunity, team context, and the kind of impact the person will make",
  "responsibilities": [
    "8 to 12 specific, action-verb-led bullet points describing day-to-day duties"
  ],
  "required_skills": [
    "List the provided skills as short descriptive requirement statements (e.g. '3+ years of hands-on experience with Spring Boot and Microservices')"
  ],
  "nice_to_have": [
    "3 to 5 additional complementary skills that would strengthen a candidate's profile"
  ],
  "qualifications": [
    "4 to 6 education and experience requirement statements tailored to the designation level"
  ],
  "what_we_offer": [
    "6 to 8 compelling benefits, growth opportunities, and company perks at Parkar Digital"
  ]
}"""


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
        data = call_claude_json(SYSTEM, user_prompt, max_tokens=2000)
    except LLMError:
        logger.exception("JD generator LLM call failed")
        raise

    return JDContent.model_validate(data)
