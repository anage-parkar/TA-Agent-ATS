"""Central configuration loaded from environment / .env.local / .env.

Env files are resolved by ABSOLUTE path so values load no matter what working
directory the backend is started from. We search in two locations:
  1. repo root  (TA-Agent-ATS/.env  and  TA-Agent-ATS/.env.local)
  2. backend/   (backend/.env       and  backend/.env.local)

Files listed later in the tuple win, so backend/.env.local has the highest
priority. This lets the project work whether .env sits at the repo root or
inside backend/ (the current layout keeps it in backend/).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND = Path(__file__).resolve().parents[1]   # …/backend
_ROOT    = Path(__file__).resolve().parents[2]   # …/TA-Agent-ATS


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            str(_ROOT    / ".env"),
            str(_ROOT    / ".env.local"),
            str(_BACKEND / ".env"),
            str(_BACKEND / ".env.local"),
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM gateway — provider is config-only (Engineering Rule 1).
    #   api → AnthropicAPIProvider (Anthropic Console API key) — production default
    #   cli → CLIProvider (Claude Code CLI, Max-plan OAuth) — local dev ONLY
    llm_provider: str = "api"
    anthropic_api_key: str = ""  # required when llm_provider="api"
    # Model alias: 'sonnet' | 'opus' | 'haiku', or a full model id. Mapped to
    # current model ids in services.llm.base.API_MODEL_ALIASES for the API.
    llm_model: str = "sonnet"
    # Anthropic SDK call tuning.
    llm_max_retries: int = 2   # SDK retries 429/5xx/connection errors w/ backoff
    llm_timeout: int = 120     # seconds per API request
    # Per-tenant rate cap (LLM calls/min). 0 = disabled. Wire higher in prod.
    llm_rate_limit_per_min: int = 0

    # CLI provider (dev only) settings.
    claude_bin: str = ""  # optional: full path to the claude executable
    llm_step_timeout: int = 180  # seconds per headless `claude -p` call
    # Max concurrent gateway calls for fan-out (e.g. scoring N candidates).
    llm_max_concurrency: int = 3

    # Multi-tenancy (Workstream A). The seeded default org/user keeps the
    # single-tenant local flow working before real login exists; the request
    # context falls back to these when no auth headers are present.
    default_tenant_id: str = "00000000-0000-0000-0000-000000000001"
    default_user_id: str = "00000000-0000-0000-0000-000000000002"
    # Trust X-User-Role / X-Tenant-Id / X-User-Id request headers to resolve the
    # principal. TRUE only because a trusted front door / dev is the caller; set
    # FALSE once real authenticated sessions land (Workstream A auth roadmap).
    trust_auth_headers: bool = True

    # Database
    database_url: str = "postgresql://postgres:localpassword@localhost:5432/ta_agent"
    supabase_url: str = "http://localhost:8000"
    supabase_service_key: str = ""
    supabase_anon_key: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Apify
    apify_api_token: str = ""
    use_mock_sourcing: bool = True

    # Talent Hunt (outbound)
    apollo_api_key: str = ""

    # Official website / careers portal channel
    use_mock_website: bool = True
    website_careers_api_url: str = ""      # generic JSON feed (fallback)
    website_partner_forward_url: str = ""  # generic external ATS forward endpoint
    # Ceipal career portal (Parkar's ATS) — public widget creds from the site.
    ceipal_api_key: str = ""
    ceipal_cp_id: str = ""
    # Ceipal ATS API (Option B — pull applicants). Admin creds, separate from
    # the public widget key. Blank = applicant pull disabled.
    ceipal_ats_email: str = ""
    ceipal_ats_password: str = ""
    ceipal_ats_api_key: str = ""

    # Google Forms — read responses via the Forms API (by form id) or the
    # linked Sheet (by sheet id). Share the form/sheet with the service-account
    # client_email. Blank SA file = built-in mock responses.
    google_sheets_sa_file: str = ""  # service-account JSON path (Forms + Sheets)
    google_form_id: str = ""         # default Google Form id (Forms API)
    google_forms_sheet_id: str = ""  # default responses Sheet id (Sheets API)

    # Email
    resend_api_key: str = ""
    outreach_from_email: str = "recruiting@example.com"
    company_name: str = "Parkar"          # branding in the email template
    company_website: str = "https://www.parkar.in"

    # LinkedIn Apply Connect webhook signing secret (verifies inbound events)
    linkedin_webhook_secret: str = ""

    # LinkedIn profile-scraper enrichment (OPT-IN, ToS/account-ban risk).
    # Requires a saved Playwright session (run scripts/create_linkedin_session.py).
    linkedin_scrape_enabled: bool = False
    linkedin_session_file: str = ""  # path to session.json

    # Google
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    google_calendar_client_id: str = ""
    google_calendar_client_secret: str = ""


settings = Settings()
