# Sub-processors — TA Agent

TA Agent (the "Processor") engages the sub-processors below to deliver the
service to customers (the "Controllers"). This list is also served at
`GET /api/legal/subprocessors`. Customers are notified of changes per the DPA.

| Sub-processor | Purpose | Data categories | Region |
|---|---|---|---|
| Anthropic | LLM inference — JD parsing, candidate scoring, reply classification | Redacted candidate profile, job text (no raw PII in scoring prompts) | US |
| Supabase / Postgres | Primary database (tenant data, pgvector, audit log) | All application data | Configurable (per-tenant `region`) |
| Resend | Transactional & outreach email delivery | Candidate name, email, message body | US / EU |
| Google (Gmail, Calendar, Forms) | Email send/receive, interview scheduling, form intake | Candidate contact + scheduling data | US / EU |
| Apify | Optional candidate sourcing | Public profile data | US / EU |
| LinkedIn | Optional job posting / Apply Connect applicant intake | Applicant-submitted data | US / EU |

Notes:
- The LLM provider receives **redacted** profiles for scoring (no name, contact,
  location, age, or affinity markers) — see `services/redaction.py`.
- EEO / diversity data is **never** sent to any sub-processor for evaluation; it
  is segregated and used only for aggregate adverse-impact reporting.
- Optional integrations (Apify, LinkedIn) are engaged only if the customer
  enables them.
