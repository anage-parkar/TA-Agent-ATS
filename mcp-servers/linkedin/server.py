"""LinkedIn Job Posting + Apply Connect MCP server.

Exposes tools to (1) read/sync your org's LinkedIn job postings and (2) read/sync
the applicants who applied to them, into the shared TA database. The ATS scoring
agent in the backend then filters & ranks the synced applicants.

Run standalone:        python server.py
Registered for the CLI via the repo-root .mcp.json (server name: "linkedin-jobs").

Set USE_MOCK_LINKEDIN=true (default) to run the whole flow with canned data
before LinkedIn partner approval; set it to false once Apply Connect is live.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

import db
import linkedin_client as li

mcp = FastMCP("linkedin-jobs")


@mcp.tool()
def check_token_health() -> str:
    """Verify the real LinkedIn OAuth token against LinkedIn (always live).

    Data tools may be in mock mode, but this always exercises the actual token
    so you can confirm your credential works before partner approval.
    """
    import os

    data_mode = "MOCK" if li.use_mock() else "LIVE"
    expiry = os.getenv("LINKEDIN_TOKEN_EXPIRY", "n/a")
    try:
        who = li.verify_live_token()
        ident = who.get("name") or who.get("localizedFirstName") or who.get("sub")
        email = who.get("email", "")
        return (
            f"✅ TOKEN VALID via {who.get('endpoint')} — identity={ident} {email}".strip()
            + f" | data_mode={data_mode}, token_expiry={expiry}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"❌ Token check failed (data_mode={data_mode}, expiry={expiry}): {exc}"


@mcp.tool()
def get_company_jobs(status: str = "LISTED") -> str:
    """List your company's LinkedIn job postings (LISTED | CLOSED | DRAFT)."""
    jobs = li.get_company_jobs(status)
    summary = [
        {"id": j.get("id"), "title": j.get("title"), "location": j.get("formattedLocation")}
        for j in jobs
    ]
    return json.dumps(summary, indent=2)


@mcp.tool()
def sync_all_jobs_to_db() -> str:
    """Sync all LISTED company job postings into the local jobs table."""
    jobs = li.get_company_jobs("LISTED")
    synced = [db.upsert_job_from_linkedin(j)["title"] for j in jobs]
    return f"Synced {len(synced)} job(s): {synced}"


@mcp.tool()
def get_job_applicants(job_linkedin_id: str) -> str:
    """List applicants who applied to a LinkedIn job posting (Apply Connect)."""
    applicants = li.get_job_applicants(job_linkedin_id)
    summary = [
        {
            "name": f"{a.get('firstName','')} {a.get('lastName','')}".strip(),
            "headline": a.get("headline"),
            "location": a.get("location"),
            "email": a.get("email"),
        }
        for a in applicants
    ]
    return json.dumps({"job_linkedin_id": job_linkedin_id, "applicants": summary}, indent=2)


@mcp.tool()
def sync_applicants_to_db(job_linkedin_id: str) -> str:
    """Pull applicants for a posting and store each as candidate + application(status=applied).

    The job must already exist in the DB (run sync_all_jobs_to_db first, or pass a
    posting that maps to a stored job). Returns how many applicants were synced.
    """
    job_db_id = db.find_job_id_by_linkedin(job_linkedin_id)
    if not job_db_id:
        # Sync the job first so applicants have something to attach to.
        jobs = {j["id"]: j for j in li.get_company_jobs("LISTED")}
        if job_linkedin_id in jobs:
            job_db_id = db.upsert_job_from_linkedin(jobs[job_linkedin_id])["id"]
        else:
            return f"Job {job_linkedin_id} not found in DB or company postings. Run sync_all_jobs_to_db."

    applicants = li.get_job_applicants(job_linkedin_id)
    count = 0
    for a in applicants:
        cand = db.upsert_applicant(a)
        db.create_application(str(job_db_id), str(cand["id"]), status="applied")
        count += 1
    return (
        f"Synced {count} applicant(s) for job {job_linkedin_id} (db job {job_db_id}). "
        f"Next: POST /api/jobs/{job_db_id}/score-applicants to rank them."
    )


@mcp.tool()
def create_test_job(title: str, description: str, location: str, remote: bool = False) -> str:
    """Create a test job posting on your LinkedIn Company Page (dev/testing)."""
    return json.dumps(li.create_test_job(title, description, location, remote))


if __name__ == "__main__":
    mcp.run()
