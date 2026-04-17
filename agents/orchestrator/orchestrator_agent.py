"""
AI Social Media Army — Orchestrator Agent  (2026 edition)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Downloads the video from Google Drive, coordinates pipeline:
  PipelineTrigger → Content → YouTube → LinkedIn → Gate (result)

FIXES:
  • Drive download: handles confirm token + streaming large files
  • video_file_id fetched from backend /jobs/internal/{id}/drive-file-id
  • gate_sender retrieved and stored; forwarded to Gate at completion
  • Proper error propagation at every stage
"""
import os
import re
import asyncio
import logging
import tempfile
from datetime import datetime
from uuid import uuid4

import httpx
from uagents import Agent, Context, Protocol
from uagents.resolver import RulesBasedResolver
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, EndSessionContent,
    TextContent, chat_protocol_spec,
)

from agents.schemas import (
    PipelineTrigger, ContentRequest, ContentResponse,
    ExecutorRequest, JobResult,
)
from agents.routing import submit_url

# ── Config ────────────────────────────────────────────────────────────────────
AGENT_SEED             = os.environ.get("ORCHESTRATOR_SEED", "orchestrator-brain-seed-v1")
BACKEND_URL            = os.environ.get("BACKEND_URL", "http://localhost:8000")
AGENT_SECRET           = os.environ.get("AGENT_SECRET", "dev-secret-123")
DEV_MODE               = os.environ.get("DEV_MODE", "true").lower() == "true"
USE_MAILBOX            = os.environ.get("USE_MAILBOX", "false").lower() == "true"
AGENT_PORT             = int(os.environ.get("ORCHESTRATOR_AGENT_PORT", "8002"))
CONTENT_AGENT_ADDRESS  = os.environ.get("CONTENT_AGENT_ADDRESS", "agent1q_CONTENT")
YOUTUBE_AGENT_ADDRESS  = os.environ.get("YOUTUBE_AGENT_ADDRESS", "agent1q_YOUTUBE")
LINKEDIN_AGENT_ADDRESS = os.environ.get("LINKEDIN_AGENT_ADDRESS", "agent1q_LINKEDIN")
GATE_AGENT_ADDRESS     = os.environ.get("GATE_AGENT_ADDRESS", "agent1q_GATE")

_my_submit = submit_url("ORCHESTRATOR_SUBMIT_URL", AGENT_PORT)
LOCAL_RULES = {
    GATE_AGENT_ADDRESS: submit_url("GATE_SUBMIT_URL", int(os.environ.get("GATE_AGENT_PORT", "8001"))),
    os.environ.get("ORCHESTRATOR_AGENT_ADDRESS", ""): _my_submit,
    CONTENT_AGENT_ADDRESS: submit_url("CONTENT_SUBMIT_URL", int(os.environ.get("CONTENT_AGENT_PORT", "8003"))),
    YOUTUBE_AGENT_ADDRESS: submit_url("YOUTUBE_SUBMIT_URL", int(os.environ.get("YOUTUBE_AGENT_PORT", "8004"))),
    LINKEDIN_AGENT_ADDRESS: submit_url("LINKEDIN_SUBMIT_URL", int(os.environ.get("LINKEDIN_AGENT_PORT", "8005"))),
    os.environ.get("SIM_AGENT_ADDRESS", ""): submit_url("SIM_SUBMIT_URL", int(os.environ.get("SIM_AGENT_PORT", "8010"))),
}
LOCAL_RULES = {k: v for k, v in LOCAL_RULES.items() if k}
resolver = RulesBasedResolver(LOCAL_RULES)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orchestrator")

# Python 3.14+ (and some Windows configs) don't create a default event loop.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

agent = Agent(
    name="social-army-orchestrator",
    seed=AGENT_SEED,
    mailbox=USE_MAILBOX,
    publish_agent_details=True,
    port=AGENT_PORT,
    endpoint=_my_submit,
    resolve=resolver,
)

# In-memory job state (survives within a session)
job_state: dict = {}


# ── Drive Download ────────────────────────────────────────────────────────────
async def download_drive_video(file_id: str) -> str:
    """Download a Google Drive public file to a temp .mp4; return local path."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    tmp.close()
    # Required simple pattern (works for public links; we still handle large-file confirm pages)
    url = f"https://drive.google.com/uc?id={file_id}"
    try:
        async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
            # First request may return HTML confirm page for large files
            resp = await client.get(url)
            if resp.status_code != 200:
                return ""

            content_type = resp.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                # Try to extract confirm token and re-request
                m = re.search(rb'confirm=([0-9A-Za-z_-]+)', resp.content)
                if m:
                    confirm = m.group(1).decode()
                    resp = await client.get(
                        f"https://drive.google.com/uc?export=download&id={file_id}&confirm={confirm}"
                    )
                else:
                    # Fallback: standard download endpoint
                    resp = await client.get(
                        f"https://drive.google.com/uc?export=download&id={file_id}"
                    )

            if resp.status_code != 200:
                return ""

            with open(tmp.name, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        size = os.path.getsize(tmp.name)
        if size < 1000:
            logger.error(f"Downloaded file too small ({size} bytes) — likely not a video")
            return ""
        logger.info(f"Video downloaded: {tmp.name} ({size // 1024} KB)")
        return tmp.name
    except Exception as e:
        logger.error(f"Drive download failed: {e}")
        return ""

def create_dummy_video_file() -> str:
    """
    Local-dev fallback so the pipeline can complete without real Drive links.
    This is NOT a valid MP4, but executors in dev mode will simulate anyway.
    """
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    tmp.write(b"AI_SOCIAL_ARMY_DUMMY_VIDEO\n")
    tmp.flush()
    tmp.close()
    return tmp.name


# ── Backend webhook ───────────────────────────────────────────────────────────
async def notify_backend(callback_url: str, payload: dict, retries: int = 3):
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    callback_url, json=payload,
                    headers={"X-Agent-Secret": AGENT_SECRET},
                )
                resp.raise_for_status()
                return
        except Exception as e:
            if attempt == retries - 1:
                logger.error(f"Webhook failed after {retries} attempts: {e}")
            else:
                await asyncio.sleep(2 ** attempt)


# ── Orchestrator Protocol ─────────────────────────────────────────────────────
orch_proto = Protocol("OrchestratorProtocol")


@orch_proto.on_message(PipelineTrigger)
async def handle_trigger(ctx: Context, sender: str, msg: PipelineTrigger):
    ctx.logger.info(f"[orch] Pipeline trigger: job={msg.job_id}")

    # Fetch video_file_id from backend (Gate stored it there)
    video_file_id = ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{BACKEND_URL}/jobs/internal/{msg.job_id}/drive-file-id",
                headers={"X-Agent-Secret": AGENT_SECRET},
            )
            if resp.status_code == 200:
                video_file_id = resp.json().get("video_file_id", "")
    except Exception as e:
        ctx.logger.warning(f"[orch] Could not get drive_file_id: {e}")

    # Fetch gate_sender from backend
    gate_sender_addr = sender  # fallback to whoever sent the trigger
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{BACKEND_URL}/jobs/internal/{msg.job_id}/gate-sender",
                headers={"X-Agent-Secret": AGENT_SECRET},
            )
            if resp.status_code == 200:
                gate_sender_addr = resp.json().get("gate_sender", sender)
    except Exception:
        pass

    job_state[msg.job_id] = {
        "job_id":          msg.job_id,
        "user_id":         msg.user_id,
        "script_text":     msg.script_text,
        "yt_access_token": msg.yt_access_token,
        "yt_refresh_token": msg.yt_refresh_token,
        "li_access_token": msg.li_access_token,
        "callback_url":    msg.callback_url,
        "video_file_id":   video_file_id,
        "video_path":      "",
        "content":         None,
        "thumbnail_base64": "",
        "youtube_result":  None,
        "linkedin_result": None,
        "stage":           "downloading",
        "gate_sender":     gate_sender_addr,
    }

    await notify_backend(msg.callback_url, {
        "job_id": msg.job_id, "step": "pipeline_started",
        "status": "running", "result_payload": {},
    })

    if not video_file_id:
        await notify_backend(msg.callback_url, {
            "job_id": msg.job_id, "step": "video_download",
            "status": "error", "result_payload": {},
            "error_message": "video_file_id not found in backend",
        })
        await ctx.send(gate_sender_addr, JobResult(
            job_id=msg.job_id, user_id=msg.user_id,
            step="video_download", status="error",
            error_message="video_file_id missing — backend may not have stored it correctly.",
        ))
        return

    ctx.logger.info(f"[orch] Downloading video file_id={video_file_id}")
    video_path = await download_drive_video(video_file_id)

    if not video_path:
        if DEV_MODE:
            video_path = create_dummy_video_file()
            ctx.logger.warning(f"[orch] Drive download failed; using dummy video: {video_path}")
            await notify_backend(msg.callback_url, {
                "job_id": msg.job_id, "step": "video_downloaded",
                "status": "success", "result_payload": {"dummy": True, "path": video_path},
            })
        else:
            err = "Failed to download video from Google Drive. Check sharing permissions."
            await notify_backend(msg.callback_url, {
                "job_id": msg.job_id, "step": "video_download",
                "status": "error", "result_payload": {}, "error_message": err,
            })
            await ctx.send(gate_sender_addr, JobResult(
                job_id=msg.job_id, user_id=msg.user_id,
                step="video_download", status="error", error_message=err,
            ))
            return

    job_state[msg.job_id]["video_path"] = video_path
    job_state[msg.job_id]["stage"]      = "content_generation"

    await notify_backend(msg.callback_url, {
        "job_id": msg.job_id, "step": "video_downloaded",
        "status": "success", "result_payload": {"size_bytes": os.path.getsize(video_path)},
    })

    # Fire content generation
    await ctx.send(CONTENT_AGENT_ADDRESS, ContentRequest(
        job_id=msg.job_id,
        user_id=msg.user_id,
        script_text=msg.script_text,
        orchestrator_address=agent.address,
    ))
    ctx.logger.info(f"[orch] job={msg.job_id} -> Content Agent")


@orch_proto.on_message(ContentResponse)
async def handle_content(ctx: Context, sender: str, msg: ContentResponse):
    ctx.logger.info(f"[orch] ContentResult: job={msg.job_id} status={msg.status}")
    state = job_state.get(msg.job_id)
    if not state:
        ctx.logger.warning(f"[orch] No state for job {msg.job_id}")
        return

    if msg.status == "error":
        await notify_backend(state["callback_url"], {
            "job_id": msg.job_id, "step": "content_generated",
            "status": "error", "result_payload": {},
            "error_message": msg.error_message,
        })
        await ctx.send(GATE_AGENT_ADDRESS, JobResult(
            job_id=msg.job_id, user_id=msg.user_id,
            step="content_generated", status="error",
            error_message=msg.error_message,
        ))
        return

    state["content"] = {
        "title":            msg.title,
        "description":      msg.description,
        "tags":             msg.tags,
        "linkedin_caption": msg.linkedin_caption,
    }
    state["thumbnail_base64"] = msg.thumbnail_base64
    state["stage"] = "youtube_upload"

    await notify_backend(state["callback_url"], {
        "job_id": msg.job_id, "step": "content_generated",
        "status": "success", "result_payload": state["content"],
    })

    await ctx.send(YOUTUBE_AGENT_ADDRESS, ExecutorRequest(
        job_id=msg.job_id,
        user_id=msg.user_id,
        video_path=state["video_path"],
        metadata=state["content"],
        thumbnail_base64=msg.thumbnail_base64,
        yt_access_token=state["yt_access_token"],
        li_access_token=state["li_access_token"],
        orchestrator_address=agent.address,
    ))
    ctx.logger.info(f"[orch] job={msg.job_id} -> YouTube Agent")


@orch_proto.on_message(JobResult)
async def handle_job_result(ctx: Context, sender: str, msg: JobResult):
    ctx.logger.info(f"[orch] JobResult: job={msg.job_id} step={msg.step} status={msg.status}")
    state = job_state.get(msg.job_id)
    if not state:
        return

    await notify_backend(state["callback_url"], {
        "job_id":        msg.job_id,
        "step":          msg.step,
        "status":        msg.status,
        "result_payload": msg.result_payload,
        "error_message": msg.error_message,
    })

    if msg.step == "youtube_uploaded" and msg.status == "success":
        state["youtube_result"] = msg.result_payload
        youtube_url = msg.result_payload.get("video_url", "")
        meta = {**state["content"], "youtube_url": youtube_url}

        await ctx.send(LINKEDIN_AGENT_ADDRESS, ExecutorRequest(
            job_id=msg.job_id,
            user_id=msg.user_id,
            video_path=state["video_path"],
            metadata=meta,
            thumbnail_base64=state["thumbnail_base64"],
            yt_access_token=state["yt_access_token"],
            li_access_token=state["li_access_token"],
            orchestrator_address=agent.address,
        ))
        ctx.logger.info(f"[orch] job={msg.job_id} -> LinkedIn Agent")

    elif msg.step == "linkedin_posted" and msg.status == "success":
        state["linkedin_result"] = msg.result_payload
        state["stage"] = "complete"

        final_payload = {
            "youtube":  state.get("youtube_result", {}),
            "linkedin": state.get("linkedin_result", {}),
            "content":  state.get("content", {}),
        }

        await notify_backend(state["callback_url"], {
            "job_id": msg.job_id, "step": "pipeline_complete",
            "status": "success", "result_payload": final_payload,
        })

        # Tell Gate to deliver result to user in ASI:ONE chat
        await ctx.send(GATE_AGENT_ADDRESS, JobResult(
            job_id=msg.job_id,
            user_id=msg.user_id,
            step="pipeline_complete",
            status="success",
            result_payload=final_payload,
        ))
        ctx.logger.info(f"[orch] job={msg.job_id} COMPLETE")

    elif msg.status == "error":
        await ctx.send(GATE_AGENT_ADDRESS, JobResult(
            job_id=msg.job_id, user_id=msg.user_id,
            step=msg.step, status="error",
            result_payload={}, error_message=msg.error_message,
        ))


# ── Chat protocol (health check via ASI:ONE) ──────────────────────────────────
chat_proto = Protocol(spec=chat_protocol_spec)


@chat_proto.on_message(ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(
        timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id
    ))
    active = sum(1 for s in job_state.values() if s.get("stage") not in ["complete", "error"])
    await ctx.send(sender, ChatMessage(
        timestamp=datetime.utcnow(), msg_id=uuid4(),
        content=[
            TextContent(type="text", text=f"Orchestrator online ✅  Active jobs: {active}  Total: {len(job_state)}"),
            EndSessionContent(type="end-session"),
        ],
    ))

@chat_proto.on_message(ChatAcknowledgement)
async def handle_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    # Required by chat_protocol_spec verification; no action needed.
    return


agent.include(orch_proto, publish_manifest=True)
agent.include(chat_proto, publish_manifest=True)

if __name__ == "__main__":
    logger.info(f"Orchestrator address: {agent.address}")
    agent.run()