"""
AI Social Media Army — Shared uAgents Message Schemas
Compatible with uagents >= 0.23.6
"""
from typing import Optional, List, Dict, Any
from uagents import Model


class PipelineTrigger(Model):
    """Gate Agent → Orchestrator: kick off the full pipeline."""
    job_id: str
    user_id: str
    video_path: str              # local path (empty → orchestrator downloads from Drive)
    script_text: str
    yt_access_token: str
    yt_refresh_token: str = ""
    li_access_token: str
    callback_url: str            # FastAPI webhook e.g. http://localhost:8000/agents/callback
    post_to_youtube: bool = True
    post_to_linkedin: bool = True


class ContentRequest(Model):
    """Orchestrator → Content Agent"""
    job_id: str
    user_id: str
    script_text: str
    orchestrator_address: str


class ContentResponse(Model):
    """Content Agent → Orchestrator"""
    job_id: str
    user_id: str
    title: str = ""
    description: str = ""
    tags: List[str] = []
    linkedin_caption: str = ""
    thumbnail_base64: str = ""   # base64-encoded PNG (empty = no thumbnail)
    status: str                  # "success" | "error"
    error_message: Optional[str] = None


class ExecutorRequest(Model):
    """Orchestrator → YouTube / LinkedIn executor agents"""
    job_id: str
    user_id: str
    video_path: str              # local path to downloaded video file
    metadata: Dict[str, Any]     # title, description, tags, linkedin_caption, youtube_url
    thumbnail_base64: str = ""
    yt_access_token: str
    li_access_token: str
    orchestrator_address: str


class JobResult(Model):
    """Executor agents → Orchestrator  |  Orchestrator → Gate Agent"""
    job_id: str
    user_id: str
    step: str        # "youtube_uploaded" | "linkedin_posted" | "pipeline_complete" | "error"
    status: str      # "success" | "error"
    result_payload: Dict[str, Any] = {}
    error_message: Optional[str] = None


# Backwards-compatible aliases (internal)
ContentResult = ContentResponse
VideoJobRequest = ExecutorRequest