# TA Agent — Agentic Talent Acquisition System

A local-first, full-stack AI application that automates the end-to-end hiring

workflow: ingest a LinkedIn job post, source and score candidates, review them
with a human in the loop, draft and send outreach, parse replies, and schedule
interviews.

All LLM calls run through your **Claude Max subscription** — no paid Anthropic
API key, **no proxy**. Each agent shells out to the authenticated Claude Code
CLI in headless mode (`claude -p`), exactly like the Contiloe sales agents.

> **How the LLM layer works:** `backend/services/llm_client.py` is the single
> source of truth. `call_claude` / `call_claude_json` run `claude -p` with our
> prompt via `--system-prompt`, parse the `--output-format json` envelope, and

robustly recover the JSON object (tolerating fences, trailing prose, and
trailing commas). Independent calls (e.g. scoring N candidates) run in a small
thread pool. Model is a CLI alias (`sonnet` / `opus` / `haiku`) via `LLM_MODEL`.

> **Current status:** far past the first vertical slice — job ingestion,
> ATS scoring, outreach (email draft/send with GDPR opt-out), reply
> classification, interview scheduling (free/busy + calendar invite), and
> scheduled background reply-polling/retention (Celery worker + beat) are all
> built, human-gated, and tested, alongside multi-tenancy/RBAC,
> prompt-injection defense, and compliance/bias infrastructure (audit log,
> adverse-impact reporting, GDPR export/erasure). What's still open: the
> LangGraph orchestrator (installed but unused — the pipeline is currently
> driven by discrete, human-triggered API calls) and an in-app Settings UI.
> See [Build order](#build-order) for the full picture.


## 1. Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Node.js 20+** | `node --version` (tested on v24) |

| **Python 3.11+** | `python --version` (tested on 3.14) |
| **Docker Desktop** | Required for Postgres (pgvector) + Redis. Installed ✅ (daemon 29.x) |
| **Claude Code CLI (authenticated)** | `npm i -g @anthropic-ai/claude-code` then `claude` to log in with your Max plan. Installed ✅ — this *is* the LLM backend |

> No proxy and no API key are involved. The app also runs **without** Docker:
> it falls back to an in-memory store. Live scoring needs the CLI authenticated;
> persistence needs Postgres.


## 2. One-time setup

```bash
# Clone, then:

```
# ── Claude Code CLI (the LLM backend — no proxy, no API key) ────
npm install -g @anthropic-ai/claude-code
claude                                  # log in with Max plan credentials

# ── Backend (Python) ───────────────────────────────────────────
cd backend
python -m venv .venv && . .venv/Scripts/activate   # Windows

# ── Frontend (Next.js) ─────────────────────────────────────────
cd frontend && npm install && cd ..

# ── Environment ────────────────────────────────────────────────
cp .env.example .env.local              # fill in tokens as you enable features

### Database migrations

Migrations apply **automatically** on first boot of a fresh Postgres volume

(mounted into `/docker-entrypoint-initdb.d`). To re-apply manually:

```bash
make migrate        # or: ./tasks.ps1 migrate   (Windows)
```


## 3. Start everything

**Windows (PowerShell)** — no `make` needed:

```powershell
./tasks.ps1 up        # Docker services (Postgres, Redis)
./tasks.ps1 dev       # backend (8001) + frontend (3001) in new windows
```

**macOS/Linux:**

```bash
make up               # docker-compose up -d
make dev              # uvicorn (8001) + next dev (3001)
```

Individual services: `./tasks.ps1 backend`, `./tasks.ps1 frontend`,
`./tasks.ps1 worker`. (No proxy to start — the agents call `claude -p` directly.)


## 4. Verify it works

```bash
# Claude Code CLI is authenticated
claude --version

# Backend (reports CLI + db status)
curl http://localhost:8001/health
#   → {"status":"ok","llm_cli":true,"database":true}

# Open the UI
#   http://localhost:3001/jobs      → paste a LinkedIn job URL → Sync
#   → redirected to /candidates     → "Source candidates" → ranked cards

Interactive API docs: **http://localhost:8001/docs**
DB GUI (Adminer): **http://localhost:8080** — system *PostgreSQL*, server `supabase-db`, user `postgres`, password `localpassword`, database `ta_agent`

### Try it without any external services

`USE_MOCK_SOURCING=true` (the default in `.env.example`) returns a built-in mock

job + 3 mock candidates, so you can exercise the full slice at zero cost. The
backend test below runs the whole flow with the LLM stubbed:

```
SYNC 200   title: Senior Backend Engineer
SOURCE 200 count=3
  95.0  Priya Sharma

  76.7  Marcus Lee
  58.3  Dana Whitfield
```


## 5. Cost breakdown

| Service | Cost |
|---------|------|

| **Claude Max plan** | $100/month flat — covers *all* LLM calls via `claude -p` |
| **Apify** (LinkedIn scraping) | ~$0.50 per run (skip with `USE_MOCK_SOURCING=true`) |
| **Resend** (email send) | Free up to 3,000 emails/month |

| **Gmail / Google Calendar** | Free |
| **Postgres / Redis** | Free — run locally in Docker |

No API key, no proxy, no per-token billing. No cloud spend except Apify
scraping runs and (above-free-tier) emails.


## 6. Troubleshooting

**`llm_cli: false` at `/health`**

The Claude Code CLI isn't authenticated or isn't on PATH. Run `claude` to log
in, confirm with `claude --version`, or set `CLAUDE_BIN` to its full path in
.env.local. The backend logs CLI status on startup and at `/health`.

**Scoring hangs / times out at 180s**
Almost always MCP auto-discovery: a project `.mcp.json` makes `claude -p` boot
those MCP servers (e.g. the LinkedIn one) on *every* call. `llm_client.py` passes

`--strict-mcp-config` so transform calls load **no** MCP servers — keep that flag.
A single cold call should be ~5–10s.

**Scoring is slow (~10–20s per candidate)**
Each `claude -p` is a cold CLI start. Independent calls run in a thread pool
capable of `LLM_MAX_CONCURRENCY` (default 3) — the Max plan limits concurrent CLI

sessions. If you see queue-timeouts, lower it to 2 (or 1). For large batches this
moves to a Celery background task (build step 9).

**Changed `.env` but nothing happened (e.g. Forms still says `(mock)`)**
`uvicorn --reload` watches `.py` files, **not `.env`**. After editing `.env`
(adding a service-account path, API key, etc.), **stop and restart** the backend

so the new settings load. The log will switch from `Forms sync (mock)` to
`Forms sync (Forms API): N responses`.

**`server closed the connection unexpectedly` (cloud Supabase)**
The Supabase pooler idle-closes connections. The pool is configured with
`check=check_connection` + `max_idle=120` to validate/recycle them, so this

should self-heal; if it persists, confirm you're on the **Session** pooler
(port 5432), not Transaction (6543).

**`Postgres unavailable — using in-memory store`**
Docker isn't running or the DB container isn't up. `./tasks.ps1 up`, then check
`docker ps` for `ta-agent-db`. Data in the in-memory store is lost on restart.

**Frontend can't reach the backend (CORS / fetch failed)**
Backend must be on `:8001`; the API base URL is `NEXT_PUBLIC_API_BASE_URL` in
`frontend/.env.local`. CORS allows `localhost:3001` and `localhost:3000`.

**Celery worker not picking up tasks / scheduled reply-poll & retention not running**
Ensure Redis is up (`docker ps`), then start **both** processes — the worker
executes tasks, but only `beat` puts them on the schedule:
`./tasks.ps1 worker` (uses `--pool=solo` on Windows) and, in a separate
window, `./tasks.ps1 beat`. Without `beat` running, `poll-email-replies` and
`enforce-retention` (`backend/tasks/celery_app.py`) never fire on their own —
you'd only see replies picked up via the on-demand
`POST /api/emails/poll-replies` call.

**Backend refuses to start: "TRUST_AUTH_HEADERS=true in APP_ENV=..."**
Trusting `X-User-*` headers is a dev convenience (any caller can be any
tenant/user/role), so outside a dev-like `APP_ENV` (`dev`, `test`, `ci`,
`local`) the backend refuses to boot with it on. Fix: `TRUST_AUTH_HEADERS=false`
(real auth), or `APP_ENV=dev` for local work. `ALLOW_TRUSTED_HEADERS_OUTSIDE_DEV=true`
overrides it only behind a front door that strips inbound `X-User-*` headers
and sets them itself. Attempts to claim the reserved `system` role or
`system:` identity are logged as `security_event` lines on the
`ta_agent.security` logger (`services/security_events.py` has a sink seam).


## Project structure

```
ta-agent/

├── docker-compose.yml      Postgres (+pgvector), Redis, Supabase Studio
├── Makefile / tasks.ps1    task runners (Unix / Windows)
├── .env.example            copy to .env.local

├── backend/                FastAPI + agents
│   ├── main.py             app + CLI/db health checks
│   ├── services/llm_client.py   ← single source of truth (drives `claude -p`)

│   ├── agents/             jd_parser, scoring, sourcing (+ outreach, etc.)
│   ├── routers/            jobs, candidates (+ emails, interviews)
│   ├── models/             Pydantic schemas (validate every LLM output)

│   └── db/                 repository + Postgres/in-memory fallback
├── mcp-servers/linkedin/   LinkedIn Job Posting + Apply Connect MCP (see its README)
├── frontend/               Next.js 14 (App Router, TS, Tailwind)

│   ├── app/                jobs, candidates, pipeline, dashboard, settings
│   ├── components/         CandidateCard, ATSScoreBadge
│   └── lib/                api client, supabase client

└── supabase/migrations/    0001_init.sql (schema + pgvector)

## Candidate sourcing: 3 channels

The candidates view is split into **three source-tagged channels**, each shown as
its own section in the UI. Every path funnels through

`services/applicants.ingest_applicant` → candidate + application (with `source`),
then `POST /api/jobs/{id}/score-applicants` ranks all unscored ones.

| # | Channel | `source` | Endpoint(s) | Today |
|---|---------|----------|-------------|-------|

| 1 | **LinkedIn Job Post** | `offsite_form`, `linkedin_apply_connect` | `POST /api/jobs/{id}/apply` (+ public `/apply/{id}`) · `POST /webhooks/linkedin/applications` | Offsite form **live**; Apply Connect ready (inert until partner approval) |
| 2 | **Microsoft / Google Forms** | `google_form` | `POST /api/jobs/{id}/sync-forms` | **Google Sheets** reader (service account) + mock fallback |
| 3 | **Talent Hunt** | `talent_hunt` | `POST /api/jobs/{id}/talent-hunt` | Criteria-filtered (skills/role/experience/location); Apollo MCP + scraper hooks, mock pool now |

```
channel (apply form / forms sync / talent hunt) → candidates + applications(+phone +resume)
  → score-applicants (ATS agent) → ranked per section → Proceed/Reject → outreach (next)

**Config:** Forms → `GOOGLE_SHEETS_SA_FILE` + `GOOGLE_FORMS_SHEET_ID` (share the
responses sheet with the service-account email). Talent Hunt → `APOLLO_API_KEY`.
All three fall back to mock data when creds are absent, so the UI works today.

See `mcp-servers/linkedin/README.md` for the LinkedIn side.

## Build order

1. ✅ Docker Compose + Makefile/tasks.ps1
2. ✅ LLM gateway — provider-swappable (`cli`: Claude Code CLI for local dev; `api`: Anthropic API key for production), prompt registry, rate limiting, usage tracking (`backend/services/llm/gateway.py`)
3. ✅ DB migrations (schema + pgvector) — applied to cloud Supabase (ap-south-1)
4. ✅ FastAPI skeleton (`main.py`, health check)
5. ✅ Job ingestion (paste-URL JD parser + LinkedIn MCP job sync)
6. ✅ ATS scoring + applications table — redacted profiles, deterministic knockouts run outside the LLM, per-job configurable weights, evidence-backed
7. ✅ Recruiter review UI (candidate list, Proceed/Reject) — human-in-the-loop gate enforced at the DB layer; no unattended reject
7b. ✅ LinkedIn MCP: job + applicant sync (Apply Connect), mock mode + score-applicants endpoint
8. ✅ Email draft + send (outreach agent + Resend/Gmail + `EmailDraftModal`) — human-approved send, GDPR opt-out link on every message
9. ✅ Email tracking — `backend/tasks/celery_app.py` + `tasks/jobs.py` schedule `poll_replies_all_tenants` (every 5 min) and `enforce_retention_all_tenants` (daily) across every tenant; run with `tasks.ps1 worker` + `tasks.ps1 beat`
10. ✅ Response parser (intent classification — `agents/response_parser.classify_reply`)
11. ✅ Interview scheduler (free/busy slot proposals + human-confirmed booking, `routers/interviews.py`)
12. ✅ Interview invite agent (+ Calendar event, logged to candidate timeline)
13. ⬜ LangGraph orchestrator — `langgraph` is an installed dependency but unused; the pipeline is driven by discrete, human-triggered API calls rather than a stateful graph
14. ✅ Pipeline Kanban UI (per-channel boards: Sourced → Reviewed → Outreach → Replied → Interview → Offer)

Beyond this original list, seven more workstreams have since shipped (see
`git log`: `9b04e07`…`425ee64`):

- Workstream 0/A — API-key LLM gateway + multi-tenancy (RLS/RBAC)
- Workstream B — candidate-as-a-person data model, audit log, explainable `ai_decisions`
- Workstream C — human-in-the-loop rejection gate (no solely-automated reject)
- Workstream D — redacted, configurable, evidence-backed scoring
- Workstream E — untrusted-input & prompt-injection defense
- Workstream F — compliance & bias infrastructure (adverse-impact reports, GDPR export/erasure, retention)
- Workstream G — engagement & scheduling hardening (opt-out enforcement, reply intent, human-gated interview confirm)

## Engineering rules (enforced)

Every LLM call goes through `call_claude` / `call_claude_json` in
`backend/services/llm_client.py` (which drives `claude -p`). The model is

never invoked any other way.
All agent calls are wrapped in try/except with structured logging.
Every LLM JSON output is validated by a Pydantic model before use.

Human-in-the-loop gates are never bypassed — no email is sent without
recruiter approval.
Outreach emails include a GDPR opt-out link on every send.

# TA-Agent-ATS
Full-stack AI application that automates the end-to-end hiring workflow: ingest a LinkedIn job post, source and score candidates, review them with a human in the loop, draft and send outreach, parse replies, and schedule interviews
# TA Agent — Agentic Talent Acquisition System

A local-first, full-stack AI application that automates the end-to-end hiring
workflow: ingest a LinkedIn job post, source and score candidates, review them
with a human in the loop, draft and send outreach, parse replies, and schedule
interviews.

All LLM calls run through your **Claude Max subscription** — no paid Anthropic
API key, **no proxy**. Each agent shells out to the authenticated Claude Code
CLI in headless mode (`claude -p`), exactly like the Contiloe sales agents.

LinkedIn scraping throttles to ≥3s between requests.
ATS score breakdowns are always stored and shown — no black-box scores.
```
TA App agents (Python) → subprocess: claude -p "<prompt>" --output-format json
                       → Claude Code CLI (Max-plan OAuth session) → Anthropic
```

> **How the LLM layer works:** `backend/services/llm_client.py` is the single
> source of truth. `call_claude` / `call_claude_json` run `claude -p` with our
> prompt via `--system-prompt`, parse the `--output-format json` envelope, and
> robustly recover the JSON object (tolerating fences, trailing prose, and
> trailing commas). Independent calls (e.g. scoring N candidates) run in a small
> thread pool. Model is a CLI alias (`sonnet` / `opus` / `haiku`) via `LLM_MODEL`.

> **Current status:** far past the first vertical slice — job ingestion,
> ATS scoring, outreach (email draft/send with GDPR opt-out), reply
> classification, interview scheduling (free/busy + calendar invite), and
> scheduled background reply-polling/retention (Celery worker + beat) are all
> built, human-gated, and tested, alongside multi-tenancy/RBAC,
> prompt-injection defense, and compliance/bias infrastructure (audit log,
> adverse-impact reporting, GDPR export/erasure). What's still open: the
> LangGraph orchestrator (installed but unused — the pipeline is currently
> driven by discrete, human-triggered API calls) and an in-app Settings UI.
> See [Build order](#build-order) for the full picture.

---

## 1. Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Node.js 20+** | `node --version` (tested on v24) |
| **Python 3.11+** | `python --version` (tested on 3.14) |
| **Docker Desktop** | Required for Postgres (pgvector) + Redis. Installed ✅ (daemon 29.x) |
| **Claude Code CLI (authenticated)** | `npm i -g @anthropic-ai/claude-code` then `claude` to log in with your Max plan. Installed ✅ — this *is* the LLM backend |

> No proxy and no API key are involved. The app also runs **without** Docker:
> it falls back to an in-memory store. Live scoring needs the CLI authenticated;
> persistence needs Postgres.

---

## 2. One-time setup

```bash
# Clone, then:

# ── Claude Code CLI (the LLM backend — no proxy, no API key) ────
npm install -g @anthropic-ai/claude-code
claude                                  # log in with Max plan credentials
claude --version                        # confirm it's authenticated

# ── Backend (Python) ───────────────────────────────────────────
cd backend
python -m venv .venv && . .venv/Scripts/activate   # Windows
# source .venv/bin/activate                        # macOS/Linux
pip install -r requirements.txt
cd ..

# ── Frontend (Next.js) ─────────────────────────────────────────
cd frontend && npm install && cd ..

# ── Environment ────────────────────────────────────────────────
cp .env.example .env.local              # fill in tokens as you enable features
```

### Database migrations

Migrations apply **automatically** on first boot of a fresh Postgres volume
(mounted into `/docker-entrypoint-initdb.d`). To re-apply manually:

```bash
make migrate        # or: ./tasks.ps1 migrate   (Windows)
```

---

## 3. Start everything

**Windows (PowerShell)** — no `make` needed:

```powershell
./tasks.ps1 up        # Docker services (Postgres, Redis)
./tasks.ps1 dev       # backend (8001) + frontend (3001) in new windows
```

**macOS/Linux:**

```bash
make up               # docker-compose up -d
make dev              # uvicorn (8001) + next dev (3001)
```

Individual services: `./tasks.ps1 backend`, `./tasks.ps1 frontend`,
`./tasks.ps1 worker`. (No proxy to start — the agents call `claude -p` directly.)

---

## 4. Verify it works

```bash
# Claude Code CLI is authenticated
claude --version

# Backend (reports CLI + db status)
curl http://localhost:8001/health
#   → {"status":"ok","llm_cli":true,"database":true}

# Open the UI
#   http://localhost:3001/jobs      → paste a LinkedIn job URL → Sync
#   → redirected to /candidates     → "Source candidates" → ranked cards
```

Interactive API docs: **http://localhost:8001/docs**
DB GUI (Adminer): **http://localhost:8080** — system *PostgreSQL*, server `supabase-db`, user `postgres`, password `localpassword`, database `ta_agent`

### Try it without any external services

`USE_MOCK_SOURCING=true` (the default in `.env.example`) returns a built-in mock
job + 3 mock candidates, so you can exercise the full slice at zero cost. The
backend test below runs the whole flow with the LLM stubbed:

```
SYNC 200   title: Senior Backend Engineer
SOURCE 200 count=3
  95.0  Priya Sharma
  76.7  Marcus Lee
  58.3  Dana Whitfield
```

---

## 5. Cost breakdown

| Service | Cost |
|---------|------|
| **Claude Max plan** | $100/month flat — covers *all* LLM calls via `claude -p` |
| **Apify** (LinkedIn scraping) | ~$0.50 per run (skip with `USE_MOCK_SOURCING=true`) |
| **Resend** (email send) | Free up to 3,000 emails/month |
| **Gmail / Google Calendar** | Free |
| **Postgres / Redis** | Free — run locally in Docker |

No API key, no proxy, no per-token billing. No cloud spend except Apify
scraping runs and (above-free-tier) emails.

---

## 6. Troubleshooting

**`llm_cli: false` at `/health`**
The Claude Code CLI isn't authenticated or isn't on PATH. Run `claude` to log
in, confirm with `claude --version`, or set `CLAUDE_BIN` to its full path in
`.env.local`. The backend logs CLI status on startup and at `/health`.

**Scoring hangs / times out at 180s**
Almost always MCP auto-discovery: a project `.mcp.json` makes `claude -p` boot
those MCP servers (e.g. the LinkedIn one) on *every* call. `llm_client.py` passes
`--strict-mcp-config` so transform calls load **no** MCP servers — keep that flag.
A single cold call should be ~5–10s.

**Scoring is slow (~10–20s per candidate)**
Each `claude -p` is a cold CLI start. Independent calls run in a thread pool
capped at `LLM_MAX_CONCURRENCY` (default 3) — the Max plan limits concurrent CLI
sessions. If you see queue-timeouts, lower it to 2 (or 1). For large batches this
moves to a Celery background task (build step 9).

**Changed `.env` but nothing happened (e.g. Forms still says `(mock)`)**
`uvicorn --reload` watches `.py` files, **not `.env`**. After editing `.env`
(adding a service-account path, API key, etc.), **stop and restart** the backend
so the new settings load. The log will switch from `Forms sync (mock)` to
`Forms sync (Forms API): N responses`.

**`server closed the connection unexpectedly` (cloud Supabase)**
The Supabase pooler idle-closes connections. The pool is configured with
`check=check_connection` + `max_idle=120` to validate/recycle them, so this
should self-heal; if it persists, confirm you're on the **Session** pooler
(port 5432), not Transaction (6543).

**`Postgres unavailable — using in-memory store`**
Docker isn't running or the DB container isn't up. `./tasks.ps1 up`, then check
`docker ps` for `ta-agent-db`. Data in the in-memory store is lost on restart.

**Frontend can't reach the backend (CORS / fetch failed)**
Backend must be on `:8001`; the API base URL is `NEXT_PUBLIC_API_BASE_URL` in
`frontend/.env.local`. CORS allows `localhost:3001` and `localhost:3000`.

**Celery worker not picking up tasks / scheduled reply-poll & retention not running**
Ensure Redis is up (`docker ps`), then start **both** processes — the worker
executes tasks, but only `beat` puts them on the schedule:
`./tasks.ps1 worker` (uses `--pool=solo` on Windows) and, in a separate
window, `./tasks.ps1 beat`. Without `beat` running, `poll-email-replies` and
`enforce-retention` (`backend/tasks/celery_app.py`) never fire on their own —
you'd only see replies picked up via the on-demand
`POST /api/emails/poll-replies` call.

**Backend refuses to start: "TRUST_AUTH_HEADERS=true in APP_ENV=..."**
Trusting `X-User-*` headers is a dev convenience (any caller can be any
tenant/user/role), so outside a dev-like `APP_ENV` (`dev`, `test`, `ci`,
`local`) the backend refuses to boot with it on. Fix: `TRUST_AUTH_HEADERS=false`
(real auth), or `APP_ENV=dev` for local work. `ALLOW_TRUSTED_HEADERS_OUTSIDE_DEV=true`
overrides it only behind a front door that strips inbound `X-User-*` headers
and sets them itself. Attempts to claim the reserved `system` role or
`system:` identity are logged as `security_event` lines on the
`ta_agent.security` logger (`services/security_events.py` has a sink seam).

---

## Project structure

```
ta-agent/
├── docker-compose.yml      Postgres (+pgvector), Redis, Supabase Studio
├── Makefile / tasks.ps1    task runners (Unix / Windows)
├── .env.example            copy to .env.local
├── backend/                FastAPI + agents
│   ├── main.py             app + CLI/db health checks
│   ├── services/llm_client.py   ← single source of truth (drives `claude -p`)
│   ├── agents/             jd_parser, scoring, sourcing (+ outreach, etc.)
│   ├── routers/            jobs, candidates (+ emails, interviews)
│   ├── models/             Pydantic schemas (validate every LLM output)
│   └── db/                 repository + Postgres/in-memory fallback
├── mcp-servers/linkedin/   LinkedIn Job Posting + Apply Connect MCP (see its README)
├── frontend/               Next.js 14 (App Router, TS, Tailwind)
│   ├── app/                jobs, candidates, pipeline, dashboard, settings
│   ├── components/         CandidateCard, ATSScoreBadge
│   └── lib/                api client, supabase client
└── supabase/migrations/    0001_init.sql (schema + pgvector)
```

## Candidate sourcing: 3 channels

The candidates view is split into **three source-tagged channels**, each shown as
its own section in the UI. Every path funnels through
`services/applicants.ingest_applicant` → candidate + application (with `source`),
then `POST /api/jobs/{id}/score-applicants` ranks all unscored ones.

| # | Channel | `source` | Endpoint(s) | Today |
|---|---------|----------|-------------|-------|
| 1 | **LinkedIn Job Post** | `offsite_form`, `linkedin_apply_connect` | `POST /api/jobs/{id}/apply` (+ public `/apply/{id}`) · `POST /webhooks/linkedin/applications` | Offsite form **live**; Apply Connect ready (inert until partner approval) |
| 2 | **Microsoft / Google Forms** | `google_form` | `POST /api/jobs/{id}/sync-forms` | **Google Sheets** reader (service account) + mock fallback |
| 3 | **Talent Hunt** | `talent_hunt` | `POST /api/jobs/{id}/talent-hunt` | Criteria-filtered (skills/role/experience/location); Apollo MCP + scraper hooks, mock pool now |

```
channel (apply form / forms sync / talent hunt) → candidates + applications(+phone +resume)
  → score-applicants (ATS agent) → ranked per section → Proceed/Reject → outreach (next)
```

**Config:** Forms → `GOOGLE_SHEETS_SA_FILE` + `GOOGLE_FORMS_SHEET_ID` (share the
responses sheet with the service-account email). Talent Hunt → `APOLLO_API_KEY`.
All three fall back to mock data when creds are absent, so the UI works today.
See `mcp-servers/linkedin/README.md` for the LinkedIn side.

## Build order

1. ✅ Docker Compose + Makefile/tasks.ps1
2. ✅ LLM gateway — provider-swappable (`cli`: Claude Code CLI for local dev; `api`: Anthropic API key for production), prompt registry, rate limiting, usage tracking (`backend/services/llm/gateway.py`)
3. ✅ DB migrations (schema + pgvector) — applied to cloud Supabase (ap-south-1)
4. ✅ FastAPI skeleton (`main.py`, health check)
5. ✅ Job ingestion (paste-URL JD parser + LinkedIn MCP job sync)
6. ✅ ATS scoring + applications table — redacted profiles, deterministic knockouts run outside the LLM, per-job configurable weights, evidence-backed
7. ✅ Recruiter review UI (candidate list, Proceed/Reject) — human-in-the-loop gate enforced at the DB layer; no unattended reject
7b. ✅ LinkedIn MCP: job + applicant sync (Apply Connect), mock mode + score-applicants endpoint
8. ✅ Email draft + send (outreach agent + Resend/Gmail + `EmailDraftModal`) — human-approved send, GDPR opt-out link on every message
9. ✅ Email tracking — `backend/tasks/celery_app.py` + `tasks/jobs.py` schedule `poll_replies_all_tenants` (every 5 min) and `enforce_retention_all_tenants` (daily) across every tenant; run with `tasks.ps1 worker` + `tasks.ps1 beat`
10. ✅ Response parser (intent classification — `agents/response_parser.classify_reply`)
11. ✅ Interview scheduler (free/busy slot proposals + human-confirmed booking, `routers/interviews.py`)
12. ✅ Interview invite agent (+ Calendar event, logged to candidate timeline)
13. ⬜ LangGraph orchestrator — `langgraph` is an installed dependency but unused; the pipeline is driven by discrete, human-triggered API calls rather than a stateful graph
14. ✅ Pipeline Kanban UI (per-channel boards: Sourced → Reviewed → Outreach → Replied → Interview → Offer)

Beyond this original list, seven more workstreams have since shipped (see
`git log`: `9b04e07`…`425ee64`):

- Workstream 0/A — API-key LLM gateway + multi-tenancy (RLS/RBAC)
- Workstream B — candidate-as-a-person data model, audit log, explainable `ai_decisions`
- Workstream C — human-in-the-loop rejection gate (no solely-automated reject)
- Workstream D — redacted, configurable, evidence-backed scoring
- Workstream E — untrusted-input & prompt-injection defense
- Workstream F — compliance & bias infrastructure (adverse-impact reports, GDPR export/erasure, retention)
- Workstream G — engagement & scheduling hardening (opt-out enforcement, reply intent, human-gated interview confirm)

## Engineering rules (enforced)

- Every LLM call goes through `call_claude` / `call_claude_json` in
  `backend/services/llm_client.py` (which drives `claude -p`). The model is
  never invoked any other way.
- All agent calls are wrapped in try/except with structured logging.
- Every LLM JSON output is validated by a Pydantic model before use.
- Human-in-the-loop gates are never bypassed — no email is sent without
  recruiter approval.
- Outreach emails include a GDPR opt-out link on every send.
- LinkedIn scraping throttles to ≥3s between requests.
- ATS score breakdowns are always stored and shown — no black-box scores.
```
