"""Resolve agent /submit URLs for local dev vs multi-host (Agentverse) deploy."""
from __future__ import annotations

import os


def submit_url(env_key: str, default_port: int) -> str:
    """
    Full URL including path .../submit.
    If env_key is unset, use http://127.0.0.1:<default_port>/submit
    """
    raw = (os.environ.get(env_key) or "").strip()
    if not raw:
        return f"http://127.0.0.1:{int(default_port)}/submit"
    raw = raw.rstrip("/")
    if raw.endswith("/submit"):
        return raw
    return raw + "/submit"
