"""
Internal Jobs API — used by agents (not by end users)
POST /jobs/internal/create         → Gate creates job record
POST /jobs/internal/set-drive      → Store Drive file ID for orchestrator
GET  /jobs/internal/{id}/drive-file-id → Orchestrator retrieves file ID
GET  /jobs/internal/{id}/gate-sender   → Gate retrieves sender for result delivery
GET  /jobs/{id}/status             → Public status endpoint
GET  /jobs/history?user_id=...
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.db.session import get_db
from app.models.job import Job, JobStep
from app.core.config import settings

router  = APIRouter(prefix="/jobs", tags=["Jobs"])
logger  = logging.getLogger("jobs-router")


def _check_agent_secret(request: Request):
    if request.headers.get("X-Agent-Secret") != settings.AGENT_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── POST /jobs/internal/create ────────────────────────────────────────────────
class InternalCreateRequest(BaseModel):
    job_id: str
    user_id: str
    video_file_id: str
    script_text: str
    yt_token: str
    li_token: str
    gate_sender: str


@router.post("/internal/create")
async def internal_create(body: InternalCreateRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.get(Job, body.job_id)
    if existing:
        return {"job_id": body.job_id, "status": existing.status, "note": "already_exists"}

    job = Job(
        id=body.job_id,
        user_id=body.user_id,
        status="pending",
        video_file_id=body.video_file_id,
        script_text=body.script_text,
        yt_token=body.yt_token,
        li_token=body.li_token,
        gate_sender=body.gate_sender,
        result_json={},
    )
    db.add(job)
    await db.commit()
    logger.info(f"Job {body.job_id} created by gate agent")
    return {"job_id": body.job_id, "status": "created"}


# ── POST /jobs/internal/set-drive ─────────────────────────────────────────────
class SetDriveRequest(BaseModel):
    job_id: str
    video_file_id: str


@router.post("/internal/set-drive")
async def set_drive(body: SetDriveRequest, db: AsyncSession = Depends(get_db)):
    await db.execute(
        update(Job).where(Job.id == body.job_id).values(video_file_id=body.video_file_id)
    )
    await db.commit()
    return {"ok": True}


# ── GET /jobs/internal/{job_id}/drive-file-id ─────────────────────────────────
@router.get("/internal/{job_id}/drive-file-id")
async def get_drive_file_id(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _check_agent_secret(request)
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"video_file_id": job.video_file_id}


# ── GET /jobs/internal/{job_id}/gate-sender ────────────────────────────────────
@router.get("/internal/{job_id}/gate-sender")
async def get_gate_sender(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _check_agent_secret(request)
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"gate_sender": job.gate_sender}


# ── GET /jobs/{job_id}/status ─────────────────────────────────────────────────
@router.get("/{job_id}/status")
async def get_status(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    steps_r = await db.execute(
        select(JobStep).where(JobStep.job_id == job_id).order_by(JobStep.created_at)
    )
    steps = steps_r.scalars().all()
    return {
        "job_id": job.id,
        "status": job.status,
        "created_at": str(job.created_at),
        "updated_at": str(job.updated_at),
        "result_json": job.result_json,
        "steps": [{"step": s.step_name, "status": s.status, "at": str(s.created_at)} for s in steps],
    }


# ── GET /jobs/history ─────────────────────────────────────────────────────────
@router.get("/history")
async def history(user_id: str, db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Job).where(Job.user_id == user_id).order_by(Job.created_at.desc()).limit(20)
    )
    jobs = r.scalars().all()
    return {"user_id": user_id, "jobs": [
        {"job_id": j.id, "status": j.status, "created_at": str(j.created_at)} for j in jobs
    ]}