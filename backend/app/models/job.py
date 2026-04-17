"""SQLAlchemy ORM models for jobs and OAuth tokens."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, Text, DateTime, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str]          = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str]     = mapped_column(String(64), index=True)
    status: Mapped[str]      = mapped_column(String(20), default="pending")  # pending|running|success|error
    video_file_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    script_text: Mapped[Optional[str]]   = mapped_column(Text, nullable=True)
    yt_token: Mapped[Optional[str]]      = mapped_column(Text, nullable=True)
    li_token: Mapped[Optional[str]]      = mapped_column(Text, nullable=True)
    gate_sender: Mapped[Optional[str]]   = mapped_column(String(200), nullable=True)
    result_json: Mapped[Optional[dict]]  = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime]         = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime]         = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    steps: Mapped[list["JobStep"]] = relationship("JobStep", back_populates="job", cascade="all, delete-orphan")


class JobStep(Base):
    __tablename__ = "job_steps"

    id: Mapped[int]           = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str]       = mapped_column(ForeignKey("jobs.id"), index=True)
    step_name: Mapped[str]    = mapped_column(String(100))
    status: Mapped[str]       = mapped_column(String(20))
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime]    = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    job: Mapped["Job"] = relationship("Job", back_populates="steps")


class OAuthToken(Base):
    """
    Stored per (user_id, provider).
    Tokens are stored encrypted-at-rest via Fernet in app.services.production.
    """

    __tablename__ = "oauth_tokens"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_oauth_user_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)  # "youtube" | "linkedin"

    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))