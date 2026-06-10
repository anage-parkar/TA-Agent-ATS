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

    # LLM — Claude Code CLI (Max plan), driven headless via `claude -p`.
    # Model is a CLI alias: 'sonnet' | 'opus' | 'haiku', or a full model id.
    llm_model: str = "sonnet"
    claude_bin: str = ""  # optional: full path to the claude executable
    llm_step_timeout: int = 180  # seconds per headless `claude -p` call
    # Max concurrent `claude -p` calls. The Max plan limits concurrent CLI
    # sessions; too many at once causes calls to queue and time out. 3 is safe.
    llm_max_concurrency: int = 3

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
