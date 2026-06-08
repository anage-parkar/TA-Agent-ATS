"""TA Agent — FastAPI application entrypoint.

Run from the backend/ directory:
    uvicorn main:app --reload --port 8001
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from db.supabase_client import close_pool, db_available
from routers import applications, candidates, dashboard, emails, jobs, sourcing, website
from services.llm_client import CLAUDE_BIN, cli_available

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ta_agent")


def _verify_llm() -> bool:
    """Confirm the Claude Code CLI (Max-plan auth) is reachable."""
    if cli_available():
        logger.info("Claude Code CLI ready at %s", CLAUDE_BIN)
        return True
    logger.warning(
        "Claude Code CLI not available. Install it (`npm i -g "
        "@anthropic-ai/claude-code`) and authenticate (`claude`), or set CLAUDE_BIN."
    )

    
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.llm_ok = _verify_llm()
    app.state.db_ok = db_available()
    if not app.state.db_ok:
        logger.warning("Database unavailable — using in-memory store (data is not persisted).")
    yield
    close_pool()


app = FastAPI(title="TA Agent API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(candidates.router)
app.include_router(applications.router)
app.include_router(sourcing.router)
app.include_router(dashboard.router)
app.include_router(website.router)
app.include_router(emails.router)

# Serve uploaded resumes (offsite-apply submissions).
_uploads = Path(__file__).resolve().parent / "uploads"
_uploads.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_uploads)), name="uploads")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "llm_cli": cli_available(),
        "database": db_available(),
    }


@app.get("/")
def root():
    return {"service": "TA Agent API", "docs": "/docs"}
