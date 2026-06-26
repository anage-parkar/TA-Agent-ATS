"""TA Agent — FastAPI application entrypoint.

Run from the backend/ directory:
    uvicorn main:app --reload --port 8001
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from db.repository import HumanActorRequired
from db.supabase_client import close_pool, db_available
from routers import admin, applications, candidates, dashboard, emails, interviews, jobs, jd_generation, legal, sourcing, website
from services.auth import bind_request_context
from services.llm import get_gateway

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ta_agent")


def _verify_llm() -> bool:
    """Confirm the configured LLM gateway provider is ready."""
    gateway = get_gateway()
    if gateway.available():
        logger.info("LLM gateway ready (provider=%s)", gateway.provider_name)
        return True
    if gateway.provider_name == "anthropic_api":
        logger.warning(
            "LLM gateway provider=api but ANTHROPIC_API_KEY is not set. Set it, "
            "or use LLM_PROVIDER=cli for local development."
        )
    else:
        logger.warning(
            "LLM gateway provider=%s not ready (Claude Code CLI unavailable).",
            gateway.provider_name,
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


app = FastAPI(
    title="TA Agent API",
    version="0.1.0",
    lifespan=lifespan,
    # Bind tenant/user/role to the request context for every route, so the
    # repository (in-memory filter + Postgres RLS) is always tenant-scoped.
    dependencies=[Depends(bind_request_context)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(HumanActorRequired)
async def _human_actor_required(request: Request, exc: HumanActorRequired):
    # A solely-automated adverse decision was attempted — refuse it.
    return JSONResponse(status_code=403, content={"detail": str(exc)})


app.include_router(jobs.router)
app.include_router(candidates.router)
app.include_router(applications.router)
app.include_router(sourcing.router)
app.include_router(dashboard.router)
app.include_router(website.router)
app.include_router(emails.router)
app.include_router(jd_generation.router)
app.include_router(admin.router)
app.include_router(legal.router)
app.include_router(interviews.router)

# Serve uploaded files (resumes + generated JD PDFs).
_uploads = Path(__file__).resolve().parent / "uploads"
_uploads.mkdir(parents=True, exist_ok=True)
(_uploads / "jds").mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_uploads)), name="uploads")


@app.get("/health")
def health():
    gateway = get_gateway()
    return {
        "status": "ok",
        "llm_provider": gateway.provider_name,
        "llm_ready": gateway.available(),
        "database": db_available(),
    }


@app.get("/")
def root():
    return {"service": "TA Agent API", "docs": "/docs"}
