# SOC 2 Readiness Baseline — TA Agent

SOC 2 (Type II) takes months of evidence collection and gates enterprise deals,
so the controls below are established now, during the first sellable slice. This
is a readiness baseline, **not** an attestation. Map to the Trust Services
Criteria (Security / Availability / Confidentiality / Processing Integrity /
Privacy).

## Controls in place (code-enforced)

| Area | Control | Where |
|---|---|---|
| Tenant isolation | Postgres Row-Level Security (FORCE) on every domain table, keyed on a per-connection `app.tenant_id`; fail-closed when unbound; proven on every PR against a real Postgres as the non-superuser `app_user` role | `supabase/migrations/0006`, `db/supabase_client.tenant_connection`, `.github/workflows/rls-verify.yml` |
| Least privilege (DB) | App connects as a non-superuser, non-BYPASSRLS role (`app_user`) | `supabase/migrations/0006` |
| Access control (app) | RBAC (5 roles) on every mutating route; admin-only tenant/compliance ops | `services/rbac.py`, routers |
| Authn (model provider) | API-key auth via `LLMGateway`; no shared/consumer credentials in prod | `services/llm/` |
| Audit logging | Append-only `activity_events` (actor, action, before/after) on every state change; AI decisions recorded with model+prompt versions + input hash | `db/repository`, `services/events.py`, `ai_decisions` |
| Human oversight | Rejections require a recorded human actor; no solely-automated adverse decisions | `db/repository.update_application` |
| Input integrity | Untrusted-input sanitization, prompt-injection defense, output guarding, attachment validation | `services/sanitize.py`, `services/attachments.py` |
| Data minimization | Scoring runs on a redacted profile; EEO data segregated and never scored | `services/redaction.py`, `eeo_records` |
| Data lifecycle | Per-candidate export + erasure; per-tenant export + purge; configurable retention with automated deletion | `db/repository`, `services/retention.py` |
| Background-job least privilege | Scheduled jobs (reply polling, retention) bind each tenant under a non-human `system` principal: no RBAC permissions, cannot satisfy the human-actor gate, audited as `actor_type=system`; the role cannot be obtained over HTTP | `services/tenant_context.use_system`, `backend/tasks/jobs.py` |
| Encryption in transit | TLS to the API, DB (Supabase pooler), and all sub-processors | infra |
| Encryption at rest | Managed-Postgres at-rest encryption; uploads on encrypted volumes | infra (provider-managed) |

## Gaps to close before audit (tracked)

- Per-region database isolation for data residency (currently single DB + region
  config + routing seam; see `organizations.region`).
- Centralized secrets management + key rotation policy (today: env files).
- Formal access-review, change-management, incident-response, and vendor-risk
  procedures (process, not code).
- Backups + restore testing; availability/DR runbook.
- Real malware scanning wired into the attachment `set_scanner()` seam.
- Penetration test + continuous vulnerability scanning.
