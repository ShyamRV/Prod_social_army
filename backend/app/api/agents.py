"""
Backend API — Agent-facing endpoints
  POST /agents/callback          — Orchestrator → backend webhook
  POST /jobs/internal/create     — Gate → create job record
  GET  /jobs/internal/{id}/drive-file-id  — Orchestrator fetches video_file_id
  GET  /jobs/internal/{id}/gate-sender    — Gate/Orchestrator fetches gate_sender address
  GET  /agents/job/{id}/live     — User-facing pipeline status
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.models.job import Job, JobStep
from app.core.config import settings

router = APIRouter(tags=["Agents & Jobs"])
logger = logging.getLogger("agents-router")


# ── Pydantic schemas ──────────────────────────────────────────────────────────
class CallbackPayload(BaseModel):
    job_id: str
    step: str
    status: str
    result_payload: dict = {}
    error_message: Optional[str] = None


class CreateJobPayload(BaseModel):
    job_id: str
    user_id: str
    video_file_id: str
    script_text: str
    yt_token: str
    li_token: str
    gate_sender: str


# ── Helper ────────────────────────────────────────────────────────────────────
def _check_secret(request: Request):
    secret = request.headers.get("X-Agent-Secret", "")
    if secret != settings.AGENT_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden: invalid agent secret")


# ── Routes ────────────────────────────────────────────────────────────────────
@router.post("/agents/callback")
async def agent_callback(body: CallbackPayload, request: Request, db: AsyncSession = Depends(get_db)):
    """Orchestrator posts pipeline step results here."""
    _check_secret(request)
    logger.info(f"Callback: job={body.job_id} step={body.step} status={body.status}")

    job = await db.get(Job, body.job_id)
    if not job:
        # Don't 404 — orchestrator may retry; just warn
        logger.warning(f"Callback for unknown job {body.job_id}")
        return {"ok": True, "warning": "job_not_found"}

    # Upsert step record
    r = await db.execute(
        select(JobStep).where(JobStep.job_id == body.job_id, JobStep.step_name == body.step)
    )
    step_row = r.scalar_one_or_none()
    if step_row is None:
        step_row = JobStep(
            job_id=body.job_id,
            step_name=body.step,
            status=body.status,
            payload=body.result_payload,
        )
        db.add(step_row)
    else:
        step_row.status  = body.status
        step_row.payload = body.result_payload

    # Advance job status
    if body.step == "pipeline_complete" and body.status == "success":
        job.status      = "success"
        job.result_json = body.result_payload
    elif body.status == "error":
        job.status      = "error"
        job.result_json = {"error": body.error_message, "step": body.step}
    elif body.step == "pipeline_started":
        job.status = "running"

    job.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@router.post("/jobs/internal/create")
async def create_job(body: CreateJobPayload, db: AsyncSession = Depends(get_db)):
    """Gate Agent creates a job record before firing the pipeline."""
    existing = await db.get(Job, body.job_id)
    if existing:
        return {"ok": True, "job_id": body.job_id, "note": "already_exists"}

    job = Job(
        id=body.job_id,
        user_id=body.user_id,
        status="pending",
        video_file_id=body.video_file_id,
        script_text=body.script_text,
        yt_token=body.yt_token,
        li_token=body.li_token,
        gate_sender=body.gate_sender,
    )
    db.add(job)
    await db.commit()
    logger.info(f"Job created: {body.job_id}")
    return {"ok": True, "job_id": body.job_id}


@router.get("/jobs/internal/{job_id}/drive-file-id")
async def get_drive_file_id(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Orchestrator calls this to fetch the Drive video file_id."""
    _check_secret(request)
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_not_found")
    return {"video_file_id": job.video_file_id or ""}


@router.get("/jobs/internal/{job_id}/gate-sender")
async def get_gate_sender(job_id: str, db: AsyncSession = Depends(get_db)):
    """Gate / Orchestrator calls this to retrieve the ASI:ONE chat sender address."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_not_found")
    return {"gate_sender": job.gate_sender or ""}


@router.get("/agents/job/{job_id}/live")
async def job_live_status(job_id: str, db: AsyncSession = Depends(get_db)):
    """Real-time pipeline step visibility for the user / dashboard."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_not_found")

    r = await db.execute(select(JobStep).where(JobStep.job_id == job_id))
    steps = r.scalars().all()

    return {
        "job_id": job.id,
        "status": job.status,
        "result": job.result_json,
        "steps": [
            {"step": s.step_name, "status": s.status, "payload": s.payload}
            for s in steps
        ],
    }


@router.get("/jobs/internal/{job_id}")
async def get_internal_job(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Agent/internal job fetch (idempotent, secured)."""
    _check_secret(request)
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_not_found")
    r = await db.execute(select(JobStep).where(JobStep.job_id == job_id).order_by(JobStep.created_at))
    steps = r.scalars().all()
    return {
        "job_id": job.id,
        "user_id": job.user_id,
        "status": job.status,
        "video_file_id": job.video_file_id,
        "gate_sender": job.gate_sender,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "result_json": job.result_json,
        "steps": [{"step": s.step_name, "status": s.status, "payload": s.payload, "created_at": s.created_at} for s in steps],
    }