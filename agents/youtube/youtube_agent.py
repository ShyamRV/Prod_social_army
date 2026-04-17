"""
AI Social Media Army — YouTube Executor Agent  (2026 edition)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Receives VideoJobRequest from Orchestrator.
Uploads the video to YouTube using the user's OAuth access token.
Returns JobResult to Orchestrator.

Uses google-api-python-client with a raw access_token credential.
"""
import os
import asyncio
import base64
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

from agents.schemas import ExecutorRequest, JobResult
from agents.routing import submit_url

# ── Config ────────────────────────────────────────────────────────────────────
AGENT_SEED = os.environ.get("YOUTUBE_AGENT_SEED", "youtube-executor-seed-v1")
AGENT_PORT = int(os.environ.get("YOUTUBE_AGENT_PORT", "8004"))
USE_MAILBOX = os.environ.get("USE_MAILBOX", "false").lower() == "true"
ORCH_ADDRESS = os.environ.get("ORCHESTRATOR_AGENT_ADDRESS", "agent1q_ORCHESTRATOR")

_my_submit = submit_url("YOUTUBE_SUBMIT_URL", AGENT_PORT)
LOCAL_RULES = {
    ORCH_ADDRESS: submit_url("ORCHESTRATOR_SUBMIT_URL", int(os.environ.get("ORCHESTRATOR_AGENT_PORT", "8002"))),
    os.environ.get("YOUTUBE_AGENT_ADDRESS", ""): _my_submit,
    os.environ.get("SIM_AGENT_ADDRESS", ""): submit_url("SIM_SUBMIT_URL", int(os.environ.get("SIM_AGENT_PORT", "8010"))),
}
LOCAL_RULES = {k: v for k, v in LOCAL_RULES.items() if k}
resolver = RulesBasedResolver(LOCAL_RULES)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("youtube-agent")

# Python 3.14+ (and some Windows configs) don't create a default event loop.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

agent = Agent(
    name="social-army-youtube",
    seed=AGENT_SEED,
    mailbox=USE_MAILBOX,
    publish_agent_details=True,
    port=AGENT_PORT,
    endpoint=_my_submit,
    resolve=resolver,
)


# ── YouTube Upload ────────────────────────────────────────────────────────────
async def upload_to_youtube(video_path: str, metadata: dict, thumbnail_b64: str,
                            access_token: str) -> dict:
    """Upload video using YouTube Data API v3 via resumable upload."""
    title       = metadata.get("title", "Untitled Video")
    description = metadata.get("description", "")
    tags        = metadata.get("tags", [])

    # Build video resource
    video_body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500],
            "categoryId": "22",   # People & Blogs
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    headers = {"Authorization": f"Bearer {access_token}"}

    # Step 1: initiate resumable upload
    file_size = os.path.getsize(video_path)
    async with httpx.AsyncClient(timeout=30) as client:
        init_resp = await client.post(
            "https://www.googleapis.com/upload/youtube/v3/videos"
            "?uploadType=resumable&part=snippet,status",
            headers={
                **headers,
                "Content-Type": "application/json",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(file_size),
            },
            json=video_body,
        )
        if init_resp.status_code not in (200, 201):
            raise RuntimeError(f"YouTube initiate upload failed: {init_resp.status_code} {init_resp.text}")

        upload_url = init_resp.headers.get("Location")
        if not upload_url:
            raise RuntimeError("No Location header in YouTube initiate response")

    # Step 2: upload file
    async with httpx.AsyncClient(timeout=600) as client:
        with open(video_path, "rb") as f:
            video_data = f.read()
        upload_resp = await client.put(
            upload_url,
            content=video_data,
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(file_size),
            },
        )
        if upload_resp.status_code not in (200, 201):
            raise RuntimeError(f"YouTube upload failed: {upload_resp.status_code} {upload_resp.text}")

        video_info = upload_resp.json()
        video_id   = video_info.get("id", "")

    if not video_id:
        raise RuntimeError("YouTube upload returned no video ID")

    # Step 3: set thumbnail (optional)
    if thumbnail_b64:
        try:
            thumb_data = base64.b64decode(thumbnail_b64)
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(
                    f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
                    f"?videoId={video_id}&uploadType=media",
                    headers={**headers, "Content-Type": "image/png"},
                    content=thumb_data,
                )
        except Exception as e:
            logger.warning(f"Thumbnail upload failed (non-critical): {e}")

    return {
        "video_id":  video_id,
        "video_url": f"https://www.youtube.com/watch?v={video_id}",
        "title":     title,
    }


# ── YouTube Protocol ──────────────────────────────────────────────────────────
yt_proto = Protocol("YouTubeProtocol")


@yt_proto.on_message(ExecutorRequest)
async def handle_yt_request(ctx: Context, sender: str, msg: ExecutorRequest):
    ctx.logger.info(f"[youtube] job={msg.job_id} — uploading")

    try:
        # Local-dev safe path: simulate if token missing/placeholder
        tok = (msg.yt_access_token or "").strip().lower()
        if (not tok) or tok in {"none", "mock", "dummy"} or tok.startswith("mock-") or tok.startswith("dev-"):
            fake_id = f"SIM_{msg.job_id[:8]}"
            result = {
                "video_id": fake_id,
                "video_url": f"https://youtube.local/{fake_id}",
                "title": msg.metadata.get("title", "Untitled Video"),
                "simulated": True,
            }
            await ctx.send(msg.orchestrator_address, JobResult(
                job_id=msg.job_id,
                user_id=msg.user_id,
                step="youtube_uploaded",
                status="success",
                result_payload=result,
            ))
            return

        result = await upload_to_youtube(
            video_path=msg.video_path,
            metadata=msg.metadata,
            thumbnail_b64=msg.thumbnail_base64,
            access_token=msg.yt_access_token,
        )
        ctx.logger.info(f"[youtube] job={msg.job_id} uploaded: {result['video_url']}")
        await ctx.send(msg.orchestrator_address, JobResult(
            job_id=msg.job_id,
            user_id=msg.user_id,
            step="youtube_uploaded",
            status="success",
            result_payload=result,
        ))
    except Exception as e:
        ctx.logger.error(f"[youtube] job={msg.job_id} error: {e}")
        await ctx.send(msg.orchestrator_address, JobResult(
            job_id=msg.job_id,
            user_id=msg.user_id,
            step="youtube_uploaded",
            status="error",
            error_message=str(e),
        ))


# ── Chat protocol ─────────────────────────────────────────────────────────────
chat_proto = Protocol(spec=chat_protocol_spec)


@chat_proto.on_message(ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(
        timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id
    ))
    await ctx.send(sender, ChatMessage(
        timestamp=datetime.utcnow(), msg_id=uuid4(),
        content=[
            TextContent(type="text", text="YouTube Agent online ✅ — ready to upload videos."),
            EndSessionContent(type="end-session"),
        ],
    ))

@chat_proto.on_message(ChatAcknowledgement)
async def handle_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    # Required by chat_protocol_spec verification; no action needed.
    return


agent.include(yt_proto, publish_manifest=True)
agent.include(chat_proto, publish_manifest=True)

if __name__ == "__main__":
    logger.info(f"YouTube Agent address: {agent.address}")
    agent.run()