"""
AI Social Media Army - Auth Router
GET /auth/token/{user_id}         → Agent fetches decrypted OAuth token
GET /auth/youtube/initiate        → Start YouTube OAuth flow
GET /auth/youtube/callback        → Exchange code for tokens
GET /auth/linkedin/initiate       → Start LinkedIn OAuth flow
GET /auth/linkedin/callback       → Exchange code for tokens
"""
import logging
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.core.config import settings
from app.models.job import OAuthToken
from app.services.production import token_service, encrypt_token, decrypt_token

router = APIRouter(prefix="/auth", tags=["Auth"])
logger = logging.getLogger("auth-router")

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/userinfo.email",
]


# ── Agent token endpoint (internal, secured by X-Agent-Secret) ─────────────────
@router.get("/token/{user_id}")
async def get_agent_token(
    user_id: str, request: Request, provider: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if request.headers.get("X-Agent-Secret") != settings.AGENT_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        if provider:
            token = await token_service.get_valid_token(db, user_id, provider)
            return {"access_token": token, "provider": provider}

        yt = None
        li = None
        try:
            yt = await token_service.get_valid_token(db, user_id, "youtube")
        except Exception:
            yt = None
        try:
            li = await token_service.get_valid_token(db, user_id, "linkedin")
        except Exception:
            li = None

        return {"user_id": user_id, "tokens": {"youtube": yt, "linkedin": li}}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Token retrieval failed: {e}")
        raise HTTPException(status_code=500, detail="Token retrieval failed")


# ── YouTube OAuth ──────────────────────────────────────────────────────────────
@router.get("/youtube/initiate")
async def youtube_initiate(user_id: str):
    scope = " ".join(YOUTUBE_SCOPES)
    url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={settings.GOOGLE_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&access_type=offline"
        f"&prompt=consent"
        f"&state={user_id}"
    )
    return {"auth_url": url}


@router.get("/youtube/callback")
async def youtube_callback(code: str, state: str, db: AsyncSession = Depends(get_db)):
    user_id = state
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    await token_service.store_token(
        db, user_id, "youtube",
        data["access_token"],
        data.get("refresh_token", ""),
        data.get("expires_in", 3600),
    )
    return {"status": "youtube_connected", "user_id": user_id}


# ── LinkedIn OAuth ─────────────────────────────────────────────────────────────
LINKEDIN_SCOPES = "r_liteprofile r_emailaddress w_member_social"


@router.get("/linkedin/initiate")
async def linkedin_initiate(user_id: str):
    url = (
        "https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code"
        f"&client_id={settings.LINKEDIN_CLIENT_ID}"
        f"&redirect_uri={settings.LINKEDIN_REDIRECT_URI}"
        f"&scope={LINKEDIN_SCOPES.replace(' ', '%20')}"
        f"&state={user_id}"
    )
    return {"auth_url": url}


@router.get("/linkedin/callback")
async def linkedin_callback(code: str, state: str, db: AsyncSession = Depends(get_db)):
    user_id = state
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.LINKEDIN_REDIRECT_URI,
                "client_id": settings.LINKEDIN_CLIENT_ID,
                "client_secret": settings.LINKEDIN_CLIENT_SECRET,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    await token_service.store_token(
        db, user_id, "linkedin",
        data["access_token"],
        data.get("refresh_token", ""),
        data.get("expires_in", 5184000),
    )
    return {"status": "linkedin_connected", "user_id": user_id}
