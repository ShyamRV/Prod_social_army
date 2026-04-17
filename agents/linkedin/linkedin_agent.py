"""
AI Social Media Army — LinkedIn Executor Agent  (2026 edition)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Receives VideoJobRequest from Orchestrator.
Posts a text UGC share to LinkedIn with the YouTube link.
Returns JobResult to Orchestrator.

Uses LinkedIn UGC Post API (v2).
"""
import os
import asyncio
import logging
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
AGENT_SEED = os.environ.get("LINKEDIN_AGENT_SEED", "linkedin-executor-seed-v1")
AGENT_PORT = int(os.environ.get("LINKEDIN_AGENT_PORT", "8005"))
USE_MAILBOX = os.environ.get("USE_MAILBOX", "false").lower() == "true"
ORCH_ADDRESS = os.environ.get("ORCHESTRATOR_AGENT_ADDRESS", "agent1q_ORCHESTRATOR")

_my_submit = submit_url("LINKEDIN_SUBMIT_URL", AGENT_PORT)
LOCAL_RULES = {
    ORCH_ADDRESS: submit_url("ORCHESTRATOR_SUBMIT_URL", int(os.environ.get("ORCHESTRATOR_AGENT_PORT", "8002"))),
    os.environ.get("LINKEDIN_AGENT_ADDRESS", ""): _my_submit,
    os.environ.get("SIM_AGENT_ADDRESS", ""): submit_url("SIM_SUBMIT_URL", int(os.environ.get("SIM_AGENT_PORT", "8010"))),
}
LOCAL_RULES = {k: v for k, v in LOCAL_RULES.items() if k}
resolver = RulesBasedResolver(LOCAL_RULES)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("linkedin-agent")

# Python 3.14+ (and some Windows configs) don't create a default event loop.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

agent = Agent(
    name="social-army-linkedin",
    seed=AGENT_SEED,
    mailbox=USE_MAILBOX,
    publish_agent_details=True,
    port=AGENT_PORT,
    endpoint=_my_submit,
    resolve=resolver,
)


# ── LinkedIn Post ─────────────────────────────────────────────────────────────
async def get_linkedin_person_urn(access_token: str) -> str:
    """Fetch the authenticated user's LinkedIn URN."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://api.linkedin.com/v2/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"LinkedIn /me failed: {resp.status_code} {resp.text}")
        data = resp.json()
        return f"urn:li:person:{data['id']}"


async def post_to_linkedin(metadata: dict, access_token: str) -> dict:
    """Create a LinkedIn UGC share post."""
    youtube_url = metadata.get("youtube_url", "")
    caption     = metadata.get("linkedin_caption", "Check out my latest video!")
    title       = metadata.get("title", "New Video")

    # Replace placeholder in caption
    post_text = caption.replace("{youtube_url}", youtube_url).replace("{{youtube_url}}", youtube_url)
    if youtube_url and youtube_url not in post_text:
        post_text = f"{post_text}\n\n{youtube_url}"

    person_urn = await get_linkedin_person_urn(access_token)

    # Build UGC post body
    body = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": post_text[:3000]},
                "shareMediaCategory": "ARTICLE",
                "media": [
                    {
                        "status": "READY",
                        "description": {"text": metadata.get("description", "")[:256]},
                        "originalUrl": youtube_url,
                        "title": {"text": title[:200]},
                    }
                ] if youtube_url else [],
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }

    if not youtube_url:
        body["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] = "NONE"
        del body["specificContent"]["com.linkedin.ugc.ShareContent"]["media"]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.linkedin.com/v2/ugcPosts",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
            json=body,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"LinkedIn post failed: {resp.status_code} {resp.text}")

        post_id  = resp.headers.get("x-restli-id", resp.json().get("id", ""))
        post_url = f"https://www.linkedin.com/feed/update/{post_id}/" if post_id else "https://www.linkedin.com/feed/"

        return {
            "post_id":  post_id,
            "post_url": post_url,
            "text":     post_text[:200],
        }


# ── LinkedIn Protocol ─────────────────────────────────────────────────────────
li_proto = Protocol("LinkedInProtocol")


@li_proto.on_message(ExecutorRequest)
async def handle_li_request(ctx: Context, sender: str, msg: ExecutorRequest):
    ctx.logger.info(f"[linkedin] job={msg.job_id} — posting")

    try:
        # Local-dev safe path: simulate if token missing/placeholder
        tok = (msg.li_access_token or "").strip().lower()
        if (not tok) or tok in {"none", "mock", "dummy"} or tok.startswith("mock-") or tok.startswith("dev-"):
            fake_id = f"SIM_{msg.job_id[:8]}"
            result = {
                "post_id": fake_id,
                "post_url": f"https://linkedin.local/{fake_id}",
                "text": (msg.metadata.get("linkedin_caption") or "Posted via AI Social Media Army")[:200],
                "simulated": True,
            }
            await ctx.send(msg.orchestrator_address, JobResult(
                job_id=msg.job_id,
                user_id=msg.user_id,
                step="linkedin_posted",
                status="success",
                result_payload=result,
            ))
            return

        result = await post_to_linkedin(
            metadata=msg.metadata,
            access_token=msg.li_access_token,
        )
        ctx.logger.info(f"[linkedin] job={msg.job_id} posted: {result['post_url']}")
        await ctx.send(msg.orchestrator_address, JobResult(
            job_id=msg.job_id,
            user_id=msg.user_id,
            step="linkedin_posted",
            status="success",
            result_payload=result,
        ))
    except Exception as e:
        ctx.logger.error(f"[linkedin] job={msg.job_id} error: {e}")
        await ctx.send(msg.orchestrator_address, JobResult(
            job_id=msg.job_id,
            user_id=msg.user_id,
            step="linkedin_posted",
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
            TextContent(type="text", text="LinkedIn Agent online ✅ — ready to post."),
            EndSessionContent(type="end-session"),
        ],
    ))

@chat_proto.on_message(ChatAcknowledgement)
async def handle_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    # Required by chat_protocol_spec verification; no action needed.
    return


agent.include(li_proto, publish_manifest=True)
agent.include(chat_proto, publish_manifest=True)

if __name__ == "__main__":
    logger.info(f"LinkedIn Agent address: {agent.address}")
    agent.run()