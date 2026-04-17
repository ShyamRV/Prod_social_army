#!/usr/bin/env python3
"""
AI Social Media Army — ONE-SHOT LAUNCHER  (2026 edition)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Start everything with a single command:

    python run_all.py

Services started:
  1. FastAPI backend      → http://localhost:8000  (docs: /docs)
  2. Gate Agent           → mailbox  (user talks to this on ASI:ONE)
  3. Orchestrator Agent   → mailbox
  4. Content Agent        → mailbox
  5. YouTube Agent        → mailbox
  6. LinkedIn Agent       → mailbox

FIRST RUN:
  • Agent addresses (agent1q...) are printed in logs
  • Copy them into config/.env under *_AGENT_ADDRESS keys
  • Restart once — addresses are now wired up

REQUIREMENTS:
  pip install -r requirements.txt
  cp config/.env.example config/.env  # then fill values
"""
import os
import sys
import time
import shutil
import signal
import subprocess
import threading

ROOT     = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(ROOT, "config", ".env")
ENV_EX   = os.path.join(ROOT, "config", ".env.example")


# ── Load .env into os.environ ─────────────────────────────────────────────────
def load_env():
    if not os.path.exists(ENV_FILE):
        if os.path.exists(ENV_EX):
            shutil.copy(ENV_EX, ENV_FILE)
            print(f"⚠️  config/.env not found — copied from .env.example\n"
                  f"   Edit config/.env and fill in your API keys, then restart.\n")
        else:
            print("⚠️  config/.env.example missing. Cannot load env.")
            return

    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if v:
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    os.environ.setdefault(k, v)


# ── Process management ────────────────────────────────────────────────────────
processes: list[dict] = []


def stream_output(proc: subprocess.Popen, prefix: str):
    for line in proc.stdout:
        # Avoid Windows console encoding crashes (cp1252).
        try:
            sys.stdout.write(f"[{prefix}] {line}")
        except UnicodeEncodeError:
            safe = line.encode("utf-8", "backslashreplace").decode("utf-8")
            sys.stdout.write(f"[{prefix}] {safe}")
        sys.stdout.flush()


def launch(name: str, cmd: list[str], cwd: str = ROOT) -> subprocess.Popen:
    env = os.environ.copy()
    # Force UTF-8 everywhere (prevents UnicodeEncodeError on Windows terminals).
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
    )
    t = threading.Thread(target=stream_output, args=(p, name), daemon=True)
    t.start()
    processes.append({"name": name, "proc": p})
    return p


def shutdown(sig=None, frame=None):
    print("\n\nShutting down all services…")
    for entry in processes:
        p = entry["proc"]
        try:
            p.terminate()
        except Exception:
            pass
    time.sleep(1)
    sys.exit(0)

def ensure_local_agent_addresses():
    """
    For local runs we want deterministic addresses without manual copying.
    uAgents addresses are derived from seed, so we can compute and export them
    before spawning any agent processes.
    """
    try:
        from uagents.crypto import Identity
    except Exception:
        return

    def addr(seed: str) -> str:
        # uAgents address is derived from (seed, index=0) deterministically.
        return Identity.from_seed(seed, 0).address

    gate_seed = os.environ.get("GATE_AGENT_SEED", "gate-agent-seed-social-army-v1")
    orch_seed = os.environ.get("ORCHESTRATOR_SEED", "orchestrator-brain-seed-v1")
    content_seed = os.environ.get("CONTENT_AGENT_SEED", "content-agent-seed-v1")
    yt_seed = os.environ.get("YOUTUBE_AGENT_SEED", "youtube-executor-seed-v1")
    li_seed = os.environ.get("LINKEDIN_AGENT_SEED", "linkedin-executor-seed-v1")
    sim_seed = os.environ.get("SIM_AGENT_SEED", "local-sim-seed-v1")

    os.environ["GATE_AGENT_ADDRESS"] = addr(gate_seed)
    os.environ["ORCHESTRATOR_AGENT_ADDRESS"] = addr(orch_seed)
    os.environ["CONTENT_AGENT_ADDRESS"] = addr(content_seed)
    os.environ["YOUTUBE_AGENT_ADDRESS"] = addr(yt_seed)
    os.environ["LINKEDIN_AGENT_ADDRESS"] = addr(li_seed)
    os.environ["SIM_AGENT_ADDRESS"] = addr(sim_seed)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    load_env()
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("USE_MAILBOX", "false")
    os.environ.setdefault("SIM_AGENT_SEED", "local-sim-seed-v1")
    os.environ.setdefault("SIM_AGENT_PORT", "8010")
    ensure_local_agent_addresses()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    python      = sys.executable
    backend_dir = os.path.join(ROOT, "backend")

    print("=" * 64)
    print("  AI Social Media Army - Starting all services")
    print("=" * 64)

    # ── 1. FastAPI backend ────────────────────────────────────────────────────
    print("\n- FastAPI backend      -> http://localhost:8000")
    backend_cmd = [
        python, "-m", "uvicorn", "app.main:app",
        "--host", "0.0.0.0", "--port", "8000",
    ]
    if os.environ.get("DEBUG", "true").lower() == "true":
        backend_cmd.append("--reload")

    launch("BACKEND", backend_cmd, cwd=backend_dir)
    time.sleep(4)  # Wait for DB tables to be created

    # ── 2. Gate Agent ─────────────────────────────────────────────────────────
    print("- Gate Agent           -> mailbox (talk to this on ASI:ONE)")
    launch("GATE", [python, "-m", "agents.gate.gate_agent"])
    time.sleep(2)

    # ── 3. Orchestrator ───────────────────────────────────────────────────────
    print("- Orchestrator Agent   -> mailbox")
    launch("ORCH", [python, "-m", "agents.orchestrator.orchestrator_agent"])
    time.sleep(2)

    # ── 4. Content Agent ──────────────────────────────────────────────────────
    print("- Content Agent        -> mailbox")
    launch("CONTENT", [python, "-m", "agents.content.content_agent"])
    time.sleep(2)

    # ── 5. YouTube Agent ──────────────────────────────────────────────────────
    print("- YouTube Agent        -> mailbox")
    launch("YOUTUBE", [python, "-m", "agents.youtube.youtube_agent"])
    time.sleep(2)

    # ── 6. LinkedIn Agent ─────────────────────────────────────────────────────
    print("- LinkedIn Agent       -> mailbox")
    launch("LINKEDIN", [python, "-m", "agents.linkedin.linkedin_agent"])
    time.sleep(1)

    # Optional: run an end-to-end local simulation (dev-safe)
    if os.environ.get("RUN_SIMULATION", "true").lower() == "true":
        print("\n- Running local simulation (Gate -> Orchestrator -> Content -> Executors)...")
        launch("SIM", [python, "-m", "tools.simulate_flow"])

    print("\n" + "=" * 64)
    print("  All services started!")
    print()
    print("  Agent addresses are deterministic from seeds (no manual copying).")
    print()
    print("  Chat with Gate Agent on: https://asi1.ai")
    print("    -> Agents tab -> search 'social-army-gate'")
    print()
    print("  Backend API docs: http://localhost:8000/docs")
    print("  Health check:    http://localhost:8000/health")
    print()
    print("  Press Ctrl+C to stop everything")
    print("=" * 64 + "\n")

    # Keep-alive with crash detection
    reported: set[int] = set()
    while True:
        time.sleep(2)
        alive: list[dict] = []
        for entry in processes:
            p = entry["proc"]
            if p.poll() is None:
                alive.append(entry)
                continue
            if p.pid not in reported:
                reported.add(p.pid)
                print(f"\n[{entry['name']}] exited with code {p.returncode}. Check logs above for errors.")
        processes[:] = alive


if __name__ == "__main__":
    main()