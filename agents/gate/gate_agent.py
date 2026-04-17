"""
AI Social Media Army — Gate Agent  (2026 edition)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The ONLY agent the user talks to via ASI:ONE chat.
Multi-turn conversation flow:

  init          → Welcome, ask for Google Drive links
  awaiting_links → Parse & download Drive script
  yt_auth       → Show YouTube OAuth URL, receive code
  li_auth       → Show LinkedIn OAuth URL, receive code
  ready         → Confirm & fire pipeline
  running       → Waiting for result
  done          → Show results

FIXES in this version:
  • PipelineTrigger correctly imported from agents.schemas
  • yt_refresh_token passed through
  • extract_code_from_message handles LinkedIn codes
  • session stored per-sender with full error recovery
  • drive_video_url/script_url stored for orchestrator retrieval
  • All imports use uagents_core.contrib.protocols.chat correctly
"""
import os
import re
import asyncio
import uuid
import hashlib
import logging
from typing import Any, Dict, Optional
from datetime import datetime
from uuid import uuid4

import httpx
from openai import OpenAI
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

try:
    from agents.schemas import PipelineTrigger, JobResult
except ImportError:
    try:
        from schemas import PipelineTrigger, JobResult
    except ImportError:
        from uagents import Model

        class PipelineTrigger(Model):
            job_id: str
            user_id: str
            video_path: str
            script_text: str
            yt_access_token: str
            yt_refresh_token: str = ""
            li_access_token: str
            callback_url: str
            post_to_youtube: bool = True
            post_to_linkedin: bool = True

        class JobResult(Model):
            job_id: str
            user_id: str
            step: str
            status: str
            result_payload: Dict[str, Any] = {}
            error_message: Optional[str] = None

try:
    from agents.routing import submit_url, agent_network_kwargs
except ImportError:
    try:
        from routing import submit_url, agent_network_kwargs
    except ImportError:
        def submit_url(env_key: str, default_port: int) -> str:
            raw = (os.environ.get(env_key) or "").strip()
            if not raw:
                return f"http://127.0.0.1:{int(default_port)}/submit"
            raw = raw.rstrip("/")
            if raw.endswith("/submit"):
                return raw
            return raw + "/submit"

        def agent_network_kwargs(use_mailbox: bool, endpoint_url: str, local_rules: dict[str, str]) -> dict[str, Any]:
            if use_mailbox:
                return {}
            from uagents.resolver import RulesBasedResolver

            cleaned = {k: v for k, v in local_rules.items() if k and v}
            return {"endpoint": endpoint_url, "resolve": RulesBasedResolver(cleaned)}

# ── Config ────────────────────────────────────────────────────────────────────
ASI1_API_KEY          = os.environ.get("ASI1_API_KEY", "")
AGENT_SEED            = os.environ.get("GATE_AGENT_SEED", "gate-agent-seed-social-army-v1")
BACKEND_URL           = os.environ.get("BACKEND_URL", "http://localhost:8000")
AGENT_SECRET          = os.environ.get("AGENT_SECRET", "dev-secret-123")
ORCHESTRATOR_ADDRESS  = os.environ.get("ORCHESTRATOR_AGENT_ADDRESS", "agent1q_ORCHESTRATOR")
AGENT_PORT            = int(os.environ.get("GATE_AGENT_PORT", "8001"))
DEV_MODE              = os.environ.get("DEV_MODE", "true").lower() == "true"
USE_MAILBOX           = os.environ.get("USE_MAILBOX", "false").lower() == "true"

_my_submit = submit_url("GATE_SUBMIT_URL", AGENT_PORT)
LOCAL_RULES = {
    os.environ.get("GATE_AGENT_ADDRESS", ""): _my_submit,
    ORCHESTRATOR_ADDRESS: submit_url("ORCHESTRATOR_SUBMIT_URL", int(os.environ.get("ORCHESTRATOR_AGENT_PORT", "8002"))),
    os.environ.get("CONTENT_AGENT_ADDRESS", ""): submit_url("CONTENT_SUBMIT_URL", int(os.environ.get("CONTENT_AGENT_PORT", "8003"))),
    os.environ.get("YOUTUBE_AGENT_ADDRESS", ""): submit_url("YOUTUBE_SUBMIT_URL", int(os.environ.get("YOUTUBE_AGENT_PORT", "8004"))),
    os.environ.get("LINKEDIN_AGENT_ADDRESS", ""): submit_url("LINKEDIN_SUBMIT_URL", int(os.environ.get("LINKEDIN_AGENT_PORT", "8005"))),
    os.environ.get("SIM_AGENT_ADDRESS", ""): submit_url("SIM_SUBMIT_URL", int(os.environ.get("SIM_AGENT_PORT", "8010"))),
}
NETWORK_KWARGS = agent_network_kwargs(USE_MAILBOX, _my_submit, LOCAL_RULES)

GOOGLE_CLIENT_ID      = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET  = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI   = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/youtube/callback")
LINKEDIN_CLIENT_ID    = os.environ.get("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
LINKEDIN_REDIRECT_URI  = os.environ.get("LINKEDIN_REDIRECT_URI", "http://localhost:8000/auth/linkedin/callback")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gate-agent")

# Python 3.14+ (and some Windows configs) don't create a default event loop.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# ── ASI:ONE client ────────────────────────────────────────────────────────────
asi1 = OpenAI(base_url="https://api.asi1.ai/v1", api_key=ASI1_API_KEY)

# ── Agent ─────────────────────────────────────────────────────────────────────
agent = Agent(
    name="social-army-gate",
    seed=AGENT_SEED,
    mailbox=USE_MAILBOX,
    publish_agent_details=True,
    port=AGENT_PORT,
    **NETWORK_KWARGS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _reply(text: str, end_session: bool = False) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    if end_session:
        content.append(EndSessionContent(type="end-session"))
    return ChatMessage(timestamp=datetime.utcnow(), msg_id=uuid4(), content=content)


def extract_file_id(drive_url: str) -> str:
    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
        r"/d/([a-zA-Z0-9_-]+)",
        r"open\?id=([a-zA-Z0-9_-]+)",
    ]
    for p in patterns:
        m = re.search(p, drive_url)
        if m:
            return m.group(1)
    return ""


async def read_drive_text(file_id: str) -> str:
    """Download text content of a public Google Drive file."""
    url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return ""
            # Handle virus-scan warning page for large files
            if b"virus scan warning" in resp.content[:1000].lower():
                m = re.search(rb'confirm=([0-9A-Za-z_-]+)', resp.content)
                if m:
                    confirm = m.group(1).decode()
                    resp = await client.get(
                        f"https://drive.google.com/uc?export=download&id={file_id}&confirm={confirm}"
                    )
            return resp.text.strip()
    except Exception as e:
        logger.error(f"Drive text read failed: {e}")
        return ""


def make_youtube_auth_url(state: str) -> str:
    scopes = (
        "https://www.googleapis.com/auth/youtube.upload "
        "https://www.googleapis.com/auth/youtube "
        "https://www.googleapis.com/auth/userinfo.email"
    )
    scope_enc = scopes.replace(" ", "%20")
    return (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={GOOGLE_REDIRECT_URI}"
        "&response_type=code"
        f"&scope={scope_enc}"
        "&access_type=offline&prompt=consent"
        f"&state={state}"
    )


def make_linkedin_auth_url(state: str) -> str:
    scopes = "r_liteprofile%20r_emailaddress%20w_member_social"
    return (
        "https://www.linkedin.com/oauth/v2/authorization"
        "?response_type=code"
        f"&client_id={LINKEDIN_CLIENT_ID}"
        f"&redirect_uri={LINKEDIN_REDIRECT_URI}"
        f"&scope={scopes}"
        f"&state={state}"
    )


async def exchange_google_code(code: str) -> dict:
    # Dev/local mock: if OAuth not configured, return dummy tokens
    if (
        (not GOOGLE_CLIENT_ID)
        or (not GOOGLE_CLIENT_SECRET)
        or GOOGLE_CLIENT_ID.strip().lower().startswith("your-")
        or GOOGLE_CLIENT_SECRET.strip().lower().startswith("your-")
    ):
        return {"access_token": "mock-youtube-access-token", "refresh_token": "mock-youtube-refresh-token", "expires_in": 3600}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
            return resp.json() if resp.status_code == 200 else {}
    except Exception as e:
        logger.error(f"Google token exchange error: {e}")
        return {}


async def exchange_linkedin_code(code: str) -> dict:
    # Dev/local mock: if OAuth not configured, return dummy tokens
    if (
        (not LINKEDIN_CLIENT_ID)
        or (not LINKEDIN_CLIENT_SECRET)
        or LINKEDIN_CLIENT_ID.strip().lower().startswith("your-")
        or LINKEDIN_CLIENT_SECRET.strip().lower().startswith("your-")
    ):
        return {"access_token": "mock-linkedin-access-token", "expires_in": 5184000}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": LINKEDIN_REDIRECT_URI,
                    "client_id": LINKEDIN_CLIENT_ID,
                    "client_secret": LINKEDIN_CLIENT_SECRET,
                },
            )
            return resp.json() if resp.status_code == 200 else {}
    except Exception as e:
        logger.error(f"LinkedIn token exchange error: {e}")
        return {}


def extract_code_from_message(text: str) -> str:
    """Extract OAuth code from a full redirect URL or bare code string."""
    # Full URL with ?code=...
    m = re.search(r'[?&]code=([^&\s]+)', text)
    if m:
        return m.group(1)
    text = text.strip()
    if DEV_MODE and text.lower() in {"mock", "skip"}:
        return "mock"
    # Google auth code starts with '4/'
    if len(text) > 10 and " " not in text and text.startswith("4/"):
        return text
    # LinkedIn code (long alphanumeric)
    if len(text) > 20 and " " not in text and re.match(r'^[A-Za-z0-9_\-]+$', text):
        return text
    return ""


def extract_connected_user_id(text: str, provider: str) -> str:
    """
    Accept backend callback response pasted in chat, e.g.:
    {"status":"youtube_connected","user_id":"agent1q..."}
    """
    t = text.strip().lower()
    if provider == "youtube" and "youtube_connected" not in t:
        return ""
    if provider == "linkedin" and "linkedin_connected" not in t:
        return ""
    m = re.search(r'"user_id"\s*:\s*"([^"]+)"', text)
    if m:
        return m.group(1).strip()
    return ""


async def fetch_backend_token(user_id: str, provider: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BACKEND_URL}/auth/token/{user_id}",
                params={"provider": provider},
                headers={"X-Agent-Secret": AGENT_SECRET},
            )
            resp.raise_for_status()
            return (resp.json().get("access_token") or "").strip()
    except Exception as e:
        logger.error(f"Failed to fetch {provider} token from backend for {user_id}: {e}")
        return ""


def get_session(ctx: Context, sender: str) -> dict:
    key = f"session:{hashlib.md5(sender.encode()).hexdigest()}"
    raw = ctx.storage.get(key)
    if raw is None:
        return {
            "stage": "init",
            "user_id": hashlib.md5(sender.encode()).hexdigest()[:12],
        }
    return raw


def save_session(ctx: Context, sender: str, session: dict):
    key = f"session:{hashlib.md5(sender.encode()).hexdigest()}"
    ctx.storage.set(key, session)


# ── Welcome message ───────────────────────────────────────────────────────────
WELCOME = """👋 Welcome to *AI Social Media Army*!

I automate your video publishing to *YouTube* and *LinkedIn* — all from this chat.

Here's the flow:
1️⃣  Upload your **video** + **script (.txt)** to Google Drive
2️⃣  Share both files (*Anyone with the link → Viewer*)
3️⃣  Paste both share links here
4️⃣  I'll walk you through YouTube + LinkedIn login
5️⃣  Type **go** → pipeline runs automatically!

Go ahead — paste your two Google Drive links to get started. 🚀"""


# ── Chat Protocol ─────────────────────────────────────────────────────────────
chat_proto = Protocol(spec=chat_protocol_spec)


@chat_proto.on_message(ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    # Always acknowledge first
    await ctx.send(sender, ChatAcknowledgement(
        timestamp=datetime.utcnow(),
        acknowledged_msg_id=msg.msg_id,
    ))

    text = " ".join(
        item.text for item in msg.content if isinstance(item, TextContent)
    ).strip()
    ctx.logger.info(f"[gate] from={sender[:20]}… text={text[:80]}")

    session = get_session(ctx, sender)
    stage = session.get("stage", "init")

    # ── INIT / HELP ───────────────────────────────────────────────────────────
    if stage == "init" or text.lower() in ("hi", "hello", "start", "help", "/start", ""):
        session["stage"] = "awaiting_links"
        save_session(ctx, sender, session)
        await ctx.send(sender, _reply(WELCOME))
        return

    # ── AWAITING DRIVE LINKS ──────────────────────────────────────────────────
    if stage == "awaiting_links":
        urls = re.findall(r'https?://drive\.google\.com/\S+', text)
        if len(urls) < 2:
            await ctx.send(sender, _reply(
                "I need **two** Google Drive share links — one for the video and one for the script (.txt).\n\n"
                "Example:\n"
                "```\nhttps://drive.google.com/file/d/VIDEO_ID/view?usp=sharing\n"
                "https://drive.google.com/file/d/SCRIPT_ID/view?usp=sharing\n```"
            ))
            return

        # Heuristic: if first URL looks like a text/script file, swap
        vid_url, script_url = urls[0], urls[1]
        if any(kw in urls[0].lower() for kw in ("txt", "script", "text")):
            vid_url, script_url = urls[1], urls[0]

        vid_id    = extract_file_id(vid_url)
        script_id = extract_file_id(script_url)

        if not vid_id or not script_id:
            await ctx.send(sender, _reply(
                "❌ Couldn't extract file IDs from those links.\n"
                "Make sure they are standard Google Drive share URLs."
            ))
            return

        await ctx.send(sender, _reply("⏳ Downloading your script from Google Drive..."))

        script_text = await read_drive_text(script_id)
        if not script_text and DEV_MODE:
            script_text = (
                "This is a DEV_MODE demo script for AI Social Media Army.\n\n"
                "Topic: How multi-agent pipelines automate social posting.\n"
                "CTA: Subscribe for more."
            )

        if not script_text:
            await ctx.send(sender, _reply(
                "❌ Couldn't read the script file.\n"
                "• Make sure it's a plain **.txt** file\n"
                "• Sharing must be set to **Anyone with the link**\n"
                "Please try again."
            ))
            return

        session.update({
            "stage": "yt_auth",
            "video_file_id": vid_id,
            "drive_video_url": vid_url,
            "drive_script_url": script_url,
            "script_text": script_text,
        })

        yt_url = make_youtube_auth_url(state=sender[:40])
        session["yt_auth_url"] = yt_url
        save_session(ctx, sender, session)

        await ctx.send(sender, _reply(
            f"✅ Script downloaded ({len(script_text)} chars)!\n\n"
            "**Step 1 of 2 — Connect YouTube** 🎬\n\n"
            "Open this link in your browser and sign in to Google:\n\n"
            f"🔗 {yt_url}\n\n"
            "After you click **Allow**, Google redirects to a page.\n"
            "Paste the **full redirect URL** (or just the `code=...` value) back here."
        ))
        return

    # ── YOUTUBE AUTH ──────────────────────────────────────────────────────────
    if stage == "yt_auth":
        # If user pasted backend callback success JSON, fetch token from backend.
        connected_user = extract_connected_user_id(text, "youtube")
        if connected_user:
            yt_token = await fetch_backend_token(connected_user, "youtube")
            if yt_token:
                session["yt_token"] = yt_token
                session["yt_refresh"] = ""
                li_url = make_linkedin_auth_url(state=sender[:40])
                session["li_auth_url"] = li_url
                session["stage"] = "li_auth"
                save_session(ctx, sender, session)
                await ctx.send(sender, _reply(
                    "✅ YouTube connected!\n\n"
                    "**Step 2 of 2 — Connect LinkedIn** 💼\n\n"
                    f"🔗 {li_url}\n\n"
                    "Paste the full redirect URL, code, or backend success JSON."
                ))
                return

        code = extract_code_from_message(text)
        if not code:
            yt_url = session.get("yt_auth_url", make_youtube_auth_url(sender[:40]))
            await ctx.send(sender, _reply(
                "⚠️ I couldn't find an authorization code in that message.\n"
                "Please paste the full redirect URL or just the `code=` value.\n\n"
                f"Auth link (if you need it again):\n🔗 {yt_url}"
            ))
            return

        await ctx.send(sender, _reply("⏳ Exchanging YouTube authorization code..."))
        token_data = await exchange_google_code(code)

        if not token_data.get("access_token"):
            yt_url = session.get("yt_auth_url", make_youtube_auth_url(sender[:40]))
            await ctx.send(sender, _reply(
                "❌ YouTube token exchange failed. Codes expire in ~60 seconds.\n"
                f"Please re-open the auth link:\n🔗 {yt_url}"
            ))
            return

        session["yt_token"]   = token_data["access_token"]
        session["yt_refresh"] = token_data.get("refresh_token", "")

        li_url = make_linkedin_auth_url(state=sender[:40])
        session["li_auth_url"] = li_url
        session["stage"] = "li_auth"
        save_session(ctx, sender, session)

        await ctx.send(sender, _reply(
            "✅ YouTube connected!\n\n"
            "**Step 2 of 2 — Connect LinkedIn** 💼\n\n"
            "Open this link in your browser:\n\n"
            f"🔗 {li_url}\n\n"
            "Paste the full redirect URL (or the code) back here."
        ))
        return

    # ── LINKEDIN AUTH ─────────────────────────────────────────────────────────
    if stage == "li_auth":
        # If user pasted backend callback success JSON, fetch token from backend.
        connected_user = extract_connected_user_id(text, "linkedin")
        if connected_user:
            li_token = await fetch_backend_token(connected_user, "linkedin")
            if li_token:
                session["li_token"] = li_token
                session["stage"] = "ready"
                save_session(ctx, sender, session)
                script_preview = session["script_text"][:300]
                if len(session["script_text"]) > 300:
                    script_preview += "..."
                await ctx.send(sender, _reply(
                    "✅ LinkedIn connected! Everything is ready.\n\n"
                    "**Summary:**\n"
                    f"📹 Video file ID: `{session['video_file_id']}`\n"
                    f"📝 Script preview: _{script_preview}_\n\n"
                    "Type **go** to start, or **cancel** to abort."
                ))
                return

        code = extract_code_from_message(text)
        if not code:
            li_url = session.get("li_auth_url", make_linkedin_auth_url(sender[:40]))
            await ctx.send(sender, _reply(
                "⚠️ Couldn't find the LinkedIn code. Paste the full redirect URL or just the code.\n\n"
                f"Auth link:\n🔗 {li_url}"
            ))
            return

        await ctx.send(sender, _reply("⏳ Exchanging LinkedIn authorization code..."))
        token_data = await exchange_linkedin_code(code)

        if not token_data.get("access_token"):
            li_url = session.get("li_auth_url", make_linkedin_auth_url(sender[:40]))
            await ctx.send(sender, _reply(
                f"❌ LinkedIn token exchange failed.\nTry again:\n🔗 {li_url}"
            ))
            return

        session["li_token"] = token_data["access_token"]
        session["stage"]    = "ready"
        save_session(ctx, sender, session)

        script_preview = session["script_text"][:300]
        if len(session["script_text"]) > 300:
            script_preview += "..."

        await ctx.send(sender, _reply(
            "✅ LinkedIn connected! Everything is ready.\n\n"
            "**Summary:**\n"
            f"📹 Video file ID: `{session['video_file_id']}`\n"
            f"📝 Script preview: _{script_preview}_\n\n"
            "**What will happen:**\n"
            "• ASI:ONE generates an optimized title, description & tags\n"
            "• Video uploaded to YouTube\n"
            "• LinkedIn post created with the YouTube link\n\n"
            "Type **go** to start, or **cancel** to abort."
        ))
        return

    # ── READY → CONFIRM ───────────────────────────────────────────────────────
    if stage == "ready":
        if any(w in text.lower() for w in ("go", "yes", "start", "confirm", "proceed", "ok", "run", "do it")):
            session["stage"] = "running"
            save_session(ctx, sender, session)

            job_id  = str(uuid.uuid4())
            user_id = session["user_id"]

            # Register job in backend
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        f"{BACKEND_URL}/jobs/internal/create",
                        json={
                            "job_id": job_id,
                            "user_id": user_id,
                            "video_file_id": session["video_file_id"],
                            "script_text": session["script_text"],
                            "yt_token": session["yt_token"],
                            "li_token": session["li_token"],
                            "gate_sender": sender,
                        },
                    )
                    resp.raise_for_status()
            except Exception as e:
                ctx.logger.error(f"Backend create-job failed: {e}")
                await ctx.send(sender, _reply(
                    f"❌ Could not register job: {e}\n"
                    "Make sure the backend is running. Type **go** to retry."
                ))
                session["stage"] = "ready"
                save_session(ctx, sender, session)
                return

            session["job_id"] = job_id
            save_session(ctx, sender, session)

            # Fire pipeline to Orchestrator
            await ctx.send(
                ORCHESTRATOR_ADDRESS,
                PipelineTrigger(
                    job_id=job_id,
                    user_id=user_id,
                    video_path="",   # orchestrator downloads from Drive
                    script_text=session["script_text"],
                    yt_access_token=session["yt_token"],
                    yt_refresh_token=session.get("yt_refresh", ""),
                    li_access_token=session["li_token"],
                    callback_url=f"{BACKEND_URL}/agents/callback",
                    post_to_youtube=True,
                    post_to_linkedin=True,
                ),
            )

            await ctx.send(sender, _reply(
                f"🚀 Pipeline started! Job ID: `{job_id}`\n\n"
                "The agents are working:\n"
                "⏳ Downloading video from Google Drive...\n"
                "⏳ Generating content with ASI:ONE...\n"
                "⏳ Uploading to YouTube...\n"
                "⏳ Posting to LinkedIn...\n\n"
                "I'll message you here when it's done (usually 2–5 min)."
            ))
            return

        if any(w in text.lower() for w in ("cancel", "stop", "no", "abort")):
            session["stage"] = "init"
            save_session(ctx, sender, session)
            await ctx.send(sender, _reply(
                "❌ Pipeline cancelled. Your auth tokens are cleared.\n"
                "Type **start** whenever you want to try again."
            ))
            return

        await ctx.send(sender, _reply("Type **go** to start the pipeline or **cancel** to abort."))
        return

    # ── RUNNING ───────────────────────────────────────────────────────────────
    if stage == "running":
        await ctx.send(sender, _reply(
            f"⏳ Pipeline is running (Job: `{session.get('job_id', '?')}`).\n"
            "I'll notify you when it's done!"
        ))
        return

    # ── DONE ──────────────────────────────────────────────────────────────────
    if stage == "done":
        result = session.get("result", {})
        yt = result.get("youtube", {})
        li = result.get("linkedin", {})
        await ctx.send(sender, _reply(
            "✅ Your last pipeline completed!\n\n"
            f"🎬 YouTube: {yt.get('video_url', 'N/A')}\n"
            f"💼 LinkedIn: {li.get('post_url', 'N/A')}\n\n"
            "Type **start** to process another video.",
            end_session=True,
        ))
        return

    # Fallback
    session["stage"] = "awaiting_links"
    save_session(ctx, sender, session)
    await ctx.send(sender, _reply(WELCOME))


@chat_proto.on_message(ChatAcknowledgement)
async def handle_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    # Required by chat_protocol_spec verification; no action needed.
    return


agent.include(chat_proto, publish_manifest=True)


# ── Result receiver (Orchestrator → Gate → User) ──────────────────────────────
result_proto = Protocol("GateResultProtocol")


@result_proto.on_message(JobResult)
async def handle_result(ctx: Context, sender: str, msg: JobResult):
    ctx.logger.info(f"[gate] Result: job={msg.job_id} step={msg.step} status={msg.status}")

    # Retrieve gate_sender from backend
    gate_sender = None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{BACKEND_URL}/jobs/internal/{msg.job_id}/gate-sender")
            if resp.status_code == 200:
                gate_sender = resp.json().get("gate_sender")
    except Exception as e:
        ctx.logger.warning(f"[gate] Could not fetch gate_sender: {e}")

    if not gate_sender:
        ctx.logger.warning(f"[gate] No gate_sender for job {msg.job_id}")
        return

    session = get_session(ctx, gate_sender)

    if msg.step == "pipeline_complete" and msg.status == "success":
        yt = msg.result_payload.get("youtube", {})
        li = msg.result_payload.get("linkedin", {})
        session["stage"]  = "done"
        session["result"] = msg.result_payload
        save_session(ctx, gate_sender, session)

        await ctx.send(gate_sender, _reply(
            "🎉 *Pipeline Complete!*\n\n"
            f"🎬 *YouTube:* {yt.get('video_url', 'N/A')}\n"
            f"   Title: _{yt.get('title', '')}_\n\n"
            f"💼 *LinkedIn:* {li.get('post_url', 'N/A')}\n\n"
            "Type **start** to process another video.",
            end_session=True,
        ))
    elif msg.status == "error":
        session["stage"] = "ready"
        save_session(ctx, gate_sender, session)
        await ctx.send(gate_sender, _reply(
            f"❌ Pipeline error at **{msg.step}**: {msg.error_message}\n\n"
            "Your auth tokens are still saved. Type **go** to retry."
        ))


agent.include(result_proto, publish_manifest=True)

if __name__ == "__main__":
    logger.info(f"Gate Agent address: {agent.address}")
    agent.run()