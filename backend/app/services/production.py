"""
AI Social Media Army - Production Services Layer (Phase 7)
Handles: OAuth token auto-refresh, retry logic, per-user rate limiting
"""
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.models.job import OAuthToken

logger = logging.getLogger("services")

# ── Encryption ────────────────────────────────────────────────────────────────
_fernet = Fernet(settings.ENCRYPTION_KEY.encode() if settings.ENCRYPTION_KEY else Fernet.generate_key())


def encrypt_token(token: str) -> str:
    return _fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    return _fernet.decrypt(encrypted.encode()).decode()


# ── Token Service ──────────────────────────────────────────────────────────────
class TokenService:
    """Manages OAuth token storage, retrieval, and auto-refresh."""

    async def get_valid_token(self, db: AsyncSession, user_id: str, provider: str) -> str:
        """Return a valid (auto-refreshed if needed) access token."""
        result = await db.execute(
            select(OAuthToken).where(
                OAuthToken.user_id == user_id,
                OAuthToken.provider == provider,
            )
        )
        token_row = result.scalar_one_or_none()
        if not token_row:
            raise ValueError(f"No {provider} token for user {user_id}")

        # Check if token expires within next 5 minutes
        if token_row.expires_at and token_row.expires_at <= datetime.now(timezone.utc) + timedelta(minutes=5):
            logger.info(f"Refreshing {provider} token for user {user_id}")
            new_token = await self._refresh_token(provider, decrypt_token(token_row.refresh_token))
            token_row.access_token = encrypt_token(new_token["access_token"])
            token_row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=new_token["expires_in"])
            await db.commit()
            return new_token["access_token"]

        return decrypt_token(token_row.access_token)

    async def store_token(
        self, db: AsyncSession, user_id: str, provider: str,
        access_token: str, refresh_token: str, expires_in: int
    ):
        result = await db.execute(
            select(OAuthToken).where(
                OAuthToken.user_id == user_id,
                OAuthToken.provider == provider,
            )
        )
        row = result.scalar_one_or_none()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        if row:
            row.access_token  = encrypt_token(access_token)
            row.refresh_token = encrypt_token(refresh_token)
            row.expires_at    = expires_at
        else:
            row = OAuthToken(
                user_id=user_id, provider=provider,
                access_token=encrypt_token(access_token),
                refresh_token=encrypt_token(refresh_token),
                expires_at=expires_at,
            )
            db.add(row)
        await db.commit()

    async def _refresh_token(self, provider: str, refresh_token: str) -> dict:
        if provider == "youtube":
            return await self._refresh_google_token(refresh_token)
        elif provider == "linkedin":
            return await self._refresh_linkedin_token(refresh_token)
        raise ValueError(f"Unknown provider: {provider}")

    async def _refresh_google_token(self, refresh_token: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return {"access_token": data["access_token"], "expires_in": data.get("expires_in", 3600)}

    async def _refresh_linkedin_token(self, refresh_token: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": settings.LINKEDIN_CLIENT_ID,
                    "client_secret": settings.LINKEDIN_CLIENT_SECRET,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return {"access_token": data["access_token"], "expires_in": data.get("expires_in", 5184000)}


token_service = TokenService()


# ── Retry Decorator ────────────────────────────────────────────────────────────
def with_retry(max_attempts: int = 3, base_delay: float = 2.0, exceptions=(Exception,)):
    """Exponential backoff retry decorator for async functions."""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts - 1:
                        raise
                    wait = base_delay * (2 ** attempt)
                    logger.warning(f"Retry {attempt+1}/{max_attempts} for {func.__name__}: {e}. Waiting {wait}s")
                    await asyncio.sleep(wait)
        return wrapper
    return decorator


# ── Per-User Rate Limiter (in-memory, upgrade to Redis for multi-instance) ──────
from collections import defaultdict
import time

_user_request_log: dict = defaultdict(list)


def check_user_rate_limit(user_id: str) -> bool:
    """Return True if user is within rate limit, False if exceeded."""
    now = time.time()
    window = 3600  # 1 hour
    limit = settings.RATE_LIMIT_PER_USER_PER_HOUR

    # Prune old entries
    _user_request_log[user_id] = [t for t in _user_request_log[user_id] if now - t < window]

    if len(_user_request_log[user_id]) >= limit:
        return False

    _user_request_log[user_id].append(now)
    return True
