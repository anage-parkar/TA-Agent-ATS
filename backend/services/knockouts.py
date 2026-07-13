"""Deterministic knockouts (Workstream D, Engineering Rule 10).

Hard requirements — work authorization, location, must-have skills — are
evaluated in plain code OUTSIDE the LLM: explainable, reproducible, and
fail-closed (if a required condition can't be confirmed from the data, the
candidate is knocked out rather than silently passed). A knocked-out candidate
is never sent to the LLM scorer.

Requirements live on the job record's `requirements` jsonb, e.g.:
    {"must_have_skills": ["Python"], "require_location": "Pune",
     "remote_ok": false, "work_auth_required": true}
When no requirements are configured, nothing is knocked out (back-compatible).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KnockoutResult:
    passed: bool
    failures: list[str] = field(default_factory=list)


def _skill_set(candidate: dict) -> set[str]:
    return {str(s).strip().lower() for s in (candidate.get("skills") or []) if s}


def evaluate(requirements: dict | None, candidate: dict) -> KnockoutResult:
    reqs = requirements or {}
    failures: list[str] = []

    # Must-have skills — every one is mandatory.
    have = _skill_set(candidate)
    for skill in reqs.get("must_have_skills") or []:
        if str(skill).strip().lower() not in have:
            failures.append(f"missing required skill: {skill}")

    # Location — only enforced when the role is not remote.
    require_location = reqs.get("require_location")
    if require_location and not reqs.get("remote_ok"):
        loc = (candidate.get("location") or "").lower()
        if not loc:
            failures.append("location required but candidate location unknown")  # fail-closed
        elif require_location.lower() not in loc:
            failures.append(f"location mismatch: requires {require_location}")

    # Work authorization — fail-closed: must be explicitly confirmed.
    if reqs.get("work_auth_required") and not candidate.get("work_authorized"):
        failures.append("work authorization not confirmed")

    return KnockoutResult(passed=not failures, failures=failures)
