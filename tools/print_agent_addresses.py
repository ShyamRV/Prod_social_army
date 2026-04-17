"""Print agent1q... addresses from seed env vars (for filling config/.env)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from uagents.crypto import Identity

ROOT = Path(__file__).resolve().parents[1]


def _addr(seed: str | None) -> str | None:
    if not seed or not str(seed).strip():
        return None
    return Identity.from_seed(str(seed).strip(), 0).address


def main() -> None:
    env_path = ROOT / "config" / ".env"
    if not env_path.is_file():
        print(f"Missing {env_path}", file=sys.stderr)
        sys.exit(1)
    load_dotenv(env_path, encoding="utf-8")
    pairs = [
        ("GATE_AGENT_SEED", "GATE_AGENT_ADDRESS"),
        ("ORCHESTRATOR_SEED", "ORCHESTRATOR_AGENT_ADDRESS"),
        ("CONTENT_AGENT_SEED", "CONTENT_AGENT_ADDRESS"),
        ("YOUTUBE_AGENT_SEED", "YOUTUBE_AGENT_ADDRESS"),
        ("LINKEDIN_AGENT_SEED", "LINKEDIN_AGENT_ADDRESS"),
        ("SIM_AGENT_SEED", "SIM_AGENT_ADDRESS"),
    ]
    for seed_key, addr_key in pairs:
        a = _addr(os.environ.get(seed_key))
        if a:
            print(f"{addr_key}={a}")


if __name__ == "__main__":
    main()
