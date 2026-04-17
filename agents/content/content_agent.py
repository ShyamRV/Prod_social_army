"""
AI Social Media Army — Content Agent  (2026 edition)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Receives ContentRequest from Orchestrator.
Calls ASI:ONE (asi1-mini) to generate:
  • YouTube title, description, tags
  • LinkedIn caption
  • Simple thumbnail (Pillow-based, no paid image API)
Returns ContentResult to Orchestrator.
"""
import os
import asyncio
import base64
import json
import logging
import io
from datetime import datetime
from uuid import uuid4

from openai import OpenAI
from uagents import Agent, Context, Protocol
from uagents.resolver import RulesBasedResolver
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, EndSessionContent,
    TextContent, chat_protocol_spec,
)

from agents.schemas import ContentRequest, ContentResponse
from agents.routing import submit_url

# ── Config ────────────────────────────────────────────────────────────────────
ASI1_API_KEY  = os.environ.get("ASI1_API_KEY", "")
AGENT_SEED    = os.environ.get("CONTENT_AGENT_SEED", "content-agent-seed-v1")
AGENT_PORT    = int(os.environ.get("CONTENT_AGENT_PORT", "8003"))
USE_MAILBOX   = os.environ.get("USE_MAILBOX", "false").lower() == "true"
ORCH_ADDRESS  = os.environ.get("ORCHESTRATOR_AGENT_ADDRESS", "agent1q_ORCHESTRATOR")

_my_submit = submit_url("CONTENT_SUBMIT_URL", AGENT_PORT)
LOCAL_RULES = {
    ORCH_ADDRESS: submit_url("ORCHESTRATOR_SUBMIT_URL", int(os.environ.get("ORCHESTRATOR_AGENT_PORT", "8002"))),
    os.environ.get("CONTENT_AGENT_ADDRESS", ""): _my_submit,
    os.environ.get("SIM_AGENT_ADDRESS", ""): submit_url("SIM_SUBMIT_URL", int(os.environ.get("SIM_AGENT_PORT", "8010"))),
}
LOCAL_RULES = {k: v for k, v in LOCAL_RULES.items() if k}
resolver = RulesBasedResolver(LOCAL_RULES)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("content-agent")

# Python 3.14+ (and some Windows configs) don't create a default event loop.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

asi1 = OpenAI(base_url="https://api.asi1.ai/v1", api_key=ASI1_API_KEY)

agent = Agent(
    name="social-army-content",
    seed=AGENT_SEED,
    mailbox=USE_MAILBOX,
    publish_agent_details=True,
    port=AGENT_PORT,
    endpoint=_my_submit,
    resolve=resolver,
)


# ── Thumbnail generation (Pillow — free, no API) ──────────────────────────────
def make_thumbnail_base64(title: str) -> str:
    """Generate a simple branded thumbnail image; returns base64 PNG."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1280, 720), color=(15, 15, 30))
        draw = ImageDraw.Draw(img)

        # Gradient overlay
        for y in range(720):
            alpha = int(80 * (1 - y / 720))
            draw.line([(0, y), (1280, y)], fill=(30, 60, 120))

        # Title text (wrapped)
        words = title.split()
        lines, line = [], []
        for w in words:
            line.append(w)
            if len(" ".join(line)) > 30:
                lines.append(" ".join(line[:-1]))
                line = [w]
        if line:
            lines.append(" ".join(line))

        y_start = 300 - len(lines) * 30
        for i, ln in enumerate(lines[:4]):
            draw.text((80, y_start + i * 70), ln, fill=(255, 255, 255))

        # Bottom bar
        draw.rectangle([(0, 640), (1280, 720)], fill=(30, 100, 200))
        draw.text((80, 655), "AI Social Media Army", fill=(255, 255, 255))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning(f"Thumbnail generation failed: {e}")
        return ""


# ── Content generation via ASI:ONE ────────────────────────────────────────────
def generate_content(script_text: str) -> dict:
    prompt = f"""You are a YouTube and LinkedIn content expert.
Given this video script, generate optimized metadata.

SCRIPT:
{script_text[:3000]}

Respond ONLY with a valid JSON object (no markdown, no extra text):
{{
  "title": "YouTube title (max 100 chars, SEO-optimized)",
  "description": "YouTube description (300-500 chars, includes hashtags)",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "linkedin_caption": "LinkedIn post caption (150-250 chars, engaging, includes YouTube link placeholder {{youtube_url}})"
}}"""

    try:
        resp = asi1.chat.completions.create(
            model="asi1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip markdown fences if any
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logger.error(f"ASI:ONE content generation failed: {e}")
        # Fallback from script
        title = script_text[:80].strip() or "New Video"
        return {
            "title": title,
            "description": script_text[:400],
            "tags": ["video", "content", "socialmedia"],
            "linkedin_caption": f"Check out my latest video! {{youtube_url}}",
        }


# ── Content Protocol ──────────────────────────────────────────────────────────
content_proto = Protocol("ContentProtocol")


@content_proto.on_message(ContentRequest)
async def handle_content_request(ctx: Context, sender: str, msg: ContentRequest):
    ctx.logger.info(f"[content] job={msg.job_id} — generating content")

    try:
        data = generate_content(msg.script_text)
        thumbnail_b64 = make_thumbnail_base64(data.get("title", "Video"))

        await ctx.send(msg.orchestrator_address, ContentResponse(
            job_id=msg.job_id,
            user_id=msg.user_id,
            title=data.get("title", ""),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            linkedin_caption=data.get("linkedin_caption", ""),
            thumbnail_base64=thumbnail_b64,
            status="success",
        ))
        ctx.logger.info(f"[content] job={msg.job_id} content sent")

    except Exception as e:
        ctx.logger.error(f"[content] job={msg.job_id} error: {e}")
        await ctx.send(msg.orchestrator_address, ContentResponse(
            job_id=msg.job_id,
            user_id=msg.user_id,
            title="", description="", tags=[], linkedin_caption="",
            thumbnail_base64="",
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
            TextContent(type="text", text="Content Agent online ✅ — ready to generate metadata."),
            EndSessionContent(type="end-session"),
        ],
    ))

@chat_proto.on_message(ChatAcknowledgement)
async def handle_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    # Required by chat_protocol_spec verification; no action needed.
    return


agent.include(content_proto, publish_manifest=True)
agent.include(chat_proto, publish_manifest=True)

if __name__ == "__main__":
    logger.info(f"Content Agent address: {agent.address}")
    agent.run()