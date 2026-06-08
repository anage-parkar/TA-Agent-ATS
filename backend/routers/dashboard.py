"""Dashboard + channel aggregate views (cross-job).

The Dashboard shows 3 channel cards with live counts; each opens a channel page
that lists that channel's candidates across all jobs.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Body, HTTPException

from db import repository

router = APIRouter(prefix="/api", tags=["dashboard"])

# Channel -> the application `source` values that belong to it.
CHANNELS: dict[str, dict] = {
    "linkedin": {
        "label": "LinkedIn Job Post",
        "subtitle": "Candidates who applied on your company page / apply link",
        "sources": ["offsite_form", "linkedin_apply_connect", "linkedin_mock"],
    },
    "forms": {
        "label": "Google Forms",
        "subtitle": "Applicants collected through your Google Forms",
        "sources": ["google_form", "microsoft_form"],
    },
    "talent-hunt": {
        "label": "Talent Hunt",
        "subtitle": "Find candidates by skills, role, experience & location",
        "sources": ["talent_hunt", "manual", "sourced"],
    },
    "website": {
        "label": "Official Website",
        "subtitle": "Jobs & applicants from your careers portal (fan-out to partner ATS)",
        "sources": ["website_portal"],
    },
}


def _parse_rows(rows: list[dict]) -> list[dict]:
    for r in rows:
        if isinstance(r.get("ats_breakdown"), str):
            try:
                r["ats_breakdown"] = json.loads(r["ats_breakdown"])
            except (json.JSONDecodeError, TypeError):
                pass
    return rows


@router.get("/dashboard/summary")
def dashboard_summary():
    """Per-channel counts for the dashboard launcher cards."""
    by_source = repository.count_applications_by_source()
    out = []
    for key, cfg in CHANNELS.items():
        total = sum(by_source.get(s, 0) for s in cfg["sources"])
        out.append(
            {"channel": key, "label": cfg["label"], "subtitle": cfg["subtitle"], "count": total}
        )
    return {"channels": out, "jobs": len(repository.list_jobs())}


@router.get("/channels/{channel}/jobs")
def channel_jobs(channel: str):
    """Jobs that have candidates in this channel (drill-down level 1)."""
    cfg = CHANNELS.get(channel)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Unknown channel: {channel}")
    # Website lists every active careers-portal job, even with no applicants yet.
    if channel == "website":
        jobs = repository.list_website_jobs()
    else:
        jobs = repository.list_jobs_with_channel_counts(cfg["sources"])
    return {"channel": channel, "label": cfg["label"], "subtitle": cfg["subtitle"], "jobs": jobs}


@router.get("/channels/{channel}")
def channel_candidates(channel: str):
    """All candidates for a channel, across every job (ranked)."""
    cfg = CHANNELS.get(channel)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Unknown channel: {channel}")
    rows = _parse_rows(repository.list_applications_by_sources(cfg["sources"]))
    return {"channel": channel, "label": cfg["label"], "subtitle": cfg["subtitle"], "candidates": rows}


_PIPELINE_STAGES = {"Sourced", "Reviewed", "Outreach", "Replied", "Interview", "Offer"}


@router.post("/applications/{application_id}/stage")
def set_application_stage(application_id: str, stage: str = Body(..., embed=True)):
    """Move an application to a pipeline stage (Kanban drag-drop)."""
    if stage not in _PIPELINE_STAGES:
        raise HTTPException(status_code=400, detail=f"Invalid stage: {stage}")
    app = repository.update_application(application_id, {"stage": stage})
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"application_id": application_id, "stage": stage}


@router.get("/applications/{application_id}")
def application_detail(application_id: str):
    """Full detail for one application (candidate + breakdown + enrichment)."""
    detail = repository.get_application_detail(application_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Application not found")
    detail = _parse_rows([detail])[0]
    cand = detail.get("candidate") or {}
    # raw_profile / enrichment may be JSON strings — parse for the UI.
    for key in ("raw_profile", "enrichment"):
        if isinstance(cand.get(key), str):
            try:
                cand[key] = json.loads(cand[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return detail
