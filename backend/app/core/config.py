"""
AI Social Media Army — Backend Settings
FREE DEV: SQLite + aiosqlite (zero cost, zero setup)
"""
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str    = "AI Social Media Army"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool      = True
    PORT: int        = 8000
    PUBLIC_BASE_URL: str = "http://localhost:8000"

    # SQLite for local dev — swap to postgresql+asyncpg://... for production
    DATABASE_URL: str = "sqlite+aiosqlite:///./social_army.db"

    # Shared secret between backend and agents
    AGENT_SECRET: str = "dev-secret-123"

    # Token encryption (Fernet). If empty, a process-local key is used (dev only).
    ENCRYPTION_KEY: str = ""

    # Simple in-memory rate limit (dev-safe)
    RATE_LIMIT_PER_USER_PER_HOUR: int = 10

    # Fetch.ai / ASI:ONE
    ASI1_API_KEY: str       = ""
    AGENTVERSE_API_KEY: str = ""

    # Agent addresses — populated after first run
    GATE_AGENT_ADDRESS:         str = "agent1q_GATE"
    ORCHESTRATOR_AGENT_ADDRESS: str = "agent1q_ORCHESTRATOR"
    CONTENT_AGENT_ADDRESS:      str = "agent1q_CONTENT"
    YOUTUBE_AGENT_ADDRESS:      str = "agent1q_YOUTUBE"
    LINKEDIN_AGENT_ADDRESS:     str = "agent1q_LINKEDIN"

    # Google OAuth
    GOOGLE_CLIENT_ID:     str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI:  str = "http://localhost:8000/auth/youtube/callback"
    GOOGLE_DRIVE_API_KEY: str = ""

    # LinkedIn OAuth
    LINKEDIN_CLIENT_ID:     str = ""
    LINKEDIN_CLIENT_SECRET: str = ""
    LINKEDIN_REDIRECT_URI:  str = "http://localhost:8000/auth/linkedin/callback"

    model_config = SettingsConfigDict(
        # Resolve repo-root `config/.env` regardless of current working directory.
        env_file=str(Path(__file__).resolve().parents[3] / "config" / ".env"),
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()