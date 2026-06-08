# LinkedIn Job + Applicant MCP Server

Wraps LinkedIn Talent Solutions so the TA Agent can:

1. **Sync your company's job postings** (Job Posting API) into the `jobs` table.
2. **Pull the applicants who applied to those postings** (Apply Connect) into
   `candidates` + `applications` (status `applied`).

The backend's ATS scoring agent then ranks those applicants
(`POST /api/jobs/{job_id}/score-applicants`).

```
LinkedIn job post → applicants apply → MCP: sync_applicants_to_db
   → candidates + applications(status=applied)
   → backend: score-applicants (claude -p ATS agent)
   → ranked list in the UI → recruiter Proceed / Reject
```

---

## Current status (2026-06-02)

- ✅ **OAuth token wired & verified live** — `check_token_health` returns `200`
  from `/v2/userinfo` (OIDC `openid profile email`), identity `anage@parkar.digital`.
  Org URN `urn:li:organization:126184204`, app Client ID `7776rfjx8asmbt`.
- ⏳ **Job Posting API** → `404 RESOURCE_NOT_FOUND` and **org/partner APIs** →
  `403 ACCESS_DENIED`: the Talent Solutions products are **not provisioned yet**
  (partner application pending). So `USE_MOCK_LINKEDIN=true` stays until approval.
- ⚠️ **Token expires 2026-08-02** and there is **no refresh token** (the Developer
  Portal generator doesn't issue one). Regenerate manually before expiry, or
  implement the 3-legged OAuth flow. `token_manager.py` raises a clear error
  when asked to refresh without one.
- ⚠️ `/rest/*` calls need a **current** `LINKEDIN_API_VERSION` (now `202605`);
  versions older than ~12 months return `426 NONEXISTENT_VERSION`.

## Run it now (mock mode — no LinkedIn access needed)

`USE_MOCK_LINKEDIN=true` (the default in `.env`) returns canned jobs + applicants
so the whole pipeline runs today, before any partnership:

```bash
cd mcp-servers/linkedin
pip install -r requirements.txt
# In Claude Code / Desktop, call the tools:
#   check_token_health
#   sync_all_jobs_to_db
#   sync_applicants_to_db  job_linkedin_id=3901234567
# Then score them from the backend:
#   curl -X POST http://localhost:8001/api/jobs/<db_job_id>/score-applicants
```

The server writes to the **same database as the backend**. If `DATABASE_URL` is
blank in this folder's `.env`, it falls back to the repo-root `.env`.

## Register with Claude Code (CLI)

Already wired via the repo-root **`.mcp.json`** (server name `linkedin-jobs`).
To (re)register manually at user scope:

```bash
claude mcp add linkedin-jobs -- python "C:/Users/abhishek.nage/TA Agent/mcp-servers/linkedin/server.py"
```

> The original guide used `claude_desktop_config.json` — that's for Claude
> **Desktop**. The TA agents run through the **CLI** (`claude -p`), which uses
> `.mcp.json` / `claude mcp add`.

---

## Going live: LinkedIn partner setup

> **Reality check.** The Job Posting API **and** Apply Connect (applicant data)
> are restricted to approved **LinkedIn Talent Solutions partners** — typically
> ATS *vendors* with a commercial agreement. A test company with a placeholder
> website is unlikely to be approved, and there is **no** public LinkedIn API to
> list applicants without it. Treat this as "build it ready, flip the switch if
> approval lands." Until then, run in mock mode.

### Corrections to the common setup guide

- **Member auth uses OpenID Connect** now: scopes `openid profile email`. The old
  `r_liteprofile` / `r_emailaddress` are **deprecated**.
- `r_organization_social` / `rw_organization_admin` are for **page social posts**,
  not job postings — they don't unlock the Job Posting API.
- The Developer **Token Generator** only issues scopes your app's **approved
  products** grant. You can't self-select Job Posting scopes pre-approval.
- Job Posting / Apply Connect endpoints in `linkedin_client.py` are marked
  `CONFIRM` — verify them against the partner Postman collection once approved
  (Apply Connect specifics are under NDA).

### Steps (when you pursue partnership)

1. Create a test LinkedIn **Company Page**; note the Organisation URN
   (`urn:li:organization:<id>`).
2. Create a **Developer App** verified with that Page → copy Client ID/Secret.
3. Add redirect URLs: `http://localhost:8001/auth/linkedin/callback`.
4. Request baseline products (Sign In with LinkedIn via **OIDC**, Share).
5. Apply to **LinkedIn Talent Solutions** for Job Posting API + Apply Connect.
6. After approval, run the 3-legged OAuth flow, paste tokens into `.env`, set
   `USE_MOCK_LINKEDIN=false`, and confirm the `CONFIRM` endpoints.

## Env (`.env`)

| Var | Purpose |
|-----|---------|
| `USE_MOCK_LINKEDIN` | `true` = canned data; `false` = live API |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | OAuth app creds |
| `LINKEDIN_ACCESS_TOKEN` / `LINKEDIN_REFRESH_TOKEN` / `LINKEDIN_TOKEN_EXPIRY` | tokens (auto-refreshed by `token_manager.py`) |
| `LINKEDIN_COMPANY_URN` | `urn:li:organization:<id>` |
| `DATABASE_URL` | shared DB; blank → falls back to repo-root `.env` |

## Files

| File | Role |
|------|------|
| `server.py` | FastMCP server + tools |
| `linkedin_client.py` | REST wrapper + mock data |
| `token_manager.py` | OAuth token refresh |
| `db.py` | psycopg writes to jobs/candidates/applications |
