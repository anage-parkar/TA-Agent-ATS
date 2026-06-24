"""Role-based access control (Workstream A).

Five roles, checked at the route/service layer. Permissions are coarse-grained
verbs; each maps to the set of roles allowed to perform it. Reads are open to
any authenticated role (not listed here); only state-changing or sensitive
actions are gated.

Usage in a router:

    from services.rbac import require_permission
    @router.post("/score", dependencies=[Depends(require_permission("application.score"))])
"""

from __future__ import annotations

from fastapi import HTTPException

from services.tenant_context import current_role

ROLES = ("admin", "recruiter", "hiring_manager", "coordinator", "interviewer")

# permission -> roles allowed. Anything not listed is treated as "all roles"
# for reads; gate every mutating/sensitive action explicitly.
PERMISSIONS: dict[str, set[str]] = {
    # Sourcing / job setup
    "job.create": {"admin", "recruiter"},
    "job.sync": {"admin", "recruiter"},
    "candidate.source": {"admin", "recruiter"},
    # Scoring + pipeline decisions
    "application.score": {"admin", "recruiter"},
    "application.decide": {"admin", "recruiter", "hiring_manager"},
    "application.stage": {"admin", "recruiter", "coordinator"},
    # Outreach / scheduling
    "email.send": {"admin", "recruiter"},
    "interview.schedule": {"admin", "recruiter", "coordinator"},
    # Interview feedback (interviewers participate here)
    "scorecard.write": {"admin", "recruiter", "hiring_manager", "interviewer"},
    # Tenant administration
    "tenant.admin": {"admin"},
}


def has_permission(role: str, permission: str) -> bool:
    allowed = PERMISSIONS.get(permission)
    if allowed is None:
        # Undeclared permission = read-ish; allow any known role.
        return role in ROLES
    return role in allowed


def require_permission(permission: str):
    """FastAPI dependency factory enforcing `permission` for the current role."""

    def _dependency() -> None:
        role = current_role()
        if not has_permission(role, permission):
            raise HTTPException(
                status_code=403,
                detail=f"role '{role}' is not permitted to perform '{permission}'",
            )

    return _dependency
