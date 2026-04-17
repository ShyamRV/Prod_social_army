# AI Social Media Army — Complete Deployment Guide
## Phase 3–7 | Fetch.ai Ecosystem | 2026

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        USER / CLIENT                                │
│              POST /jobs/create  (video + script)                    │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────────┐
│                   FASTAPI BACKEND                                   │
│  /jobs/create → /agents/trigger → /agents/callback (webhook)       │
│  Supabase Postgres · Cloudflare R2 · Per-user rate limiting         │
└────────────┬───────────────────────────────┬────────────────────────┘
             │  PipelineTrigger msg           │  JobResult webhooks
             │  (via Agentverse)              │  (HTTP callbacks)
┌────────────▼───────────────────────────────┘
│      ORCHESTRATOR AGENT (Agentverse/local)                         │
│      social-army-orchestrator                                       │
│      Manages state, retries, routes to sub-agents                  │
└────┬──────────────┬──────────────────┬──────────────────────────────┘
     │              │                  │
     ▼              ▼                  ▼
┌─────────┐  ┌────────────┐   ┌───────────────┐
│ CONTENT │  │  YOUTUBE   │   │   LINKEDIN    │
│  AGENT  │  │   AGENT    │   │    AGENT      │
│ ASI:ONE │  │ YT Data v3 │   │ LI API v2     │
│ LLM gen │  │ OAuth upload│  │ OAuth post    │
└─────────┘  └────────────┘   └───────────────┘
     │              │                  │
     └──────────────┴──────────────────┘
              ContentResult / JobResult
              (back to Orchestrator)
```

---

## End-to-End Sequence Diagram

```
User          Backend       Orchestrator    ContentAgent   YouTubeAgent   LinkedInAgent
 │                │               │               │               │               │
 │ POST /jobs/create              │               │               │               │
 │─────────────▶ │               │               │               │               │
 │               │ R2 upload      │               │               │               │
 │               │ DB insert      │               │               │               │
 │               │ PipelineTrigger│               │               │               │
 │               │───────────────▶│               │               │               │
 │               │               │ ContentRequest │               │               │
 │               │               │───────────────▶│               │               │
 │               │               │               │ ASI:ONE API   │               │
 │               │               │               │ generate()    │               │
 │               │               │  ContentResult│               │               │
 │               │               │◀──────────────│               │               │
 │               │◀ /callback    │               │               │               │
 │               │ content_generated              │               │               │
 │               │               │ VideoJobReq   │               │               │
 │               │               │───────────────────────────────▶               │
 │               │               │               │ YT Data API   │               │
 │               │               │               │ upload()      │               │
 │               │               │  JobResult    │               │               │
 │               │               │◀───────────────────────────────               │
 │               │◀ /callback    │               │               │               │
 │               │ youtube_uploaded               │               │               │
 │               │               │ VideoJobReq(+youtube_url)     │               │
 │               │               │───────────────────────────────────────────────▶
 │               │               │               │               │ LI API post() │
 │               │               │  JobResult    │               │               │
 │               │               │◀──────────────────────────────────────────────│
 │               │◀ /callback    │               │               │               │
 │               │ pipeline_complete              │               │               │
 │ GET /jobs/{id}/status          │               │               │               │
 │─────────────▶ │               │               │               │               │
 │ ◀ {youtube_url, linkedin_url} │               │               │               │
```

---

## Step-by-Step Deployment

### Step 1 — Prerequisites

```bash
pip install uagents uagents-core openai httpx
```

Create accounts:
- https://asi1.ai/dashboard/api-keys → get ASI1_API_KEY
- https://agentverse.ai → get AGENTVERSE_API_KEY
- https://supabase.com → create project → get DATABASE_URL
- https://dash.cloudflare.com → R2 → get R2 credentials
- https://console.cloud.google.com → YouTube Data API v3 credentials
- https://www.linkedin.com/developers → create app

---

### Step 2 — Get Agent Addresses (run each once)

Run each agent locally once to get its address, then add to `.env`:

```bash
# Terminal 1
cd agents
python content/content_agent.py
# → Look for: "Starting agent with address: agent1q..."
# → Set CONTENT_AGENT_ADDRESS=agent1q...

# Terminal 2
python youtube/youtube_agent.py
# → Set YOUTUBE_AGENT_ADDRESS=agent1q...

# Terminal 3
python linkedin/linkedin_agent.py
# → Set LINKEDIN_AGENT_ADDRESS=agent1q...

# Terminal 4
python orchestrator/orchestrator_agent.py
# → Set ORCHESTRATOR_AGENT_ADDRESS=agent1q...
```

---

### Step 3 — Fill .env

```bash
cp config/.env.example config/.env
# Edit config/.env with all values from Step 1 & 2
```

---

### Step 4 — Deploy to Agentverse (Cloud Hosting)

Option A: **Agentverse Hosted Agents** (recommended)
1. Go to https://agentverse.ai → My Agents → + New Agent
2. Create 4 agents: `social-content-generator`, `youtube-executor`, `linkedin-executor`, `social-army-orchestrator`
3. Upload the corresponding Python file for each
4. Add secrets in Agentverse UI (ASI1_API_KEY, BACKEND_URL, etc.)
5. Click Deploy → copy the agent address

Option B: **Render / Railway** (for local agents with mailbox)
```bash
# Push agents/ to GitHub
# Create Background Worker on Render for each agent
# Set env vars in Render dashboard
# Render logs will show agent addresses
```

---

### Step 5 — Deploy Backend

```bash
# Railway (recommended)
railway init
railway up

# Or Docker Compose locally
docker-compose up --build
```

---

### Step 6 — Test End-to-End

```bash
# 1. Create user
curl -X POST http://localhost:8000/users/create \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com"}'
# → {"user_id": "uuid-here", ...}

# 2. Connect YouTube OAuth
curl http://localhost:8000/auth/youtube/initiate?user_id=YOUR_USER_ID
# → Open auth_url in browser, complete OAuth

# 3. Connect LinkedIn OAuth
curl http://localhost:8000/auth/linkedin/initiate?user_id=YOUR_USER_ID

# 4. Upload video and trigger full pipeline
curl -X POST http://localhost:8000/jobs/create \
  -F "user_id=YOUR_USER_ID" \
  -F "script_text=This video is about building AI agents with Fetch.ai and ASI:ONE..." \
  -F "post_to_youtube=true" \
  -F "post_to_linkedin=true" \
  -F "video=@/path/to/your/video.mp4"
# → {"job_id": "uuid", "status": "pending", ...}

# 5. Poll job status
curl http://localhost:8000/jobs/JOB_ID/status
# → Shows each pipeline step as it completes

# 6. Full live status
curl http://localhost:8000/agents/job/JOB_ID/live
```

---

## Agent Discovery on ASI:ONE

Once your agents are on Agentverse with `publish_agent_details=True` and the chat protocol included, users can find them via:
- https://agentverse.ai/marketplace → search "social-army"
- ASI:ONE chat → enable Agents toggle → ask it to run your pipeline

Tag your agents with:
```
![tag:social-media](https://img.shields.io/badge/social--media-3D8BD3)
![tag:youtube-automation](https://img.shields.io/badge/youtube-red)
![tag:linkedin-automation](https://img.shields.io/badge/linkedin-0077B5)
![tag:asi1-llm-agent](https://img.shields.io/badge/asi1-3D8BD3)
```

---

## Phase 7 Checklist — Production Hardening

| Feature | Implementation | File |
|---|---|---|
| Per-user rate limiting | `check_user_rate_limit()` (in-memory → Redis for multi-instance) | `services/production.py` |
| OAuth token auto-refresh | `TokenService.get_valid_token()` with expiry check | `services/production.py` |
| AES-256 token encryption | Fernet symmetric encryption | `services/production.py` |
| Webhook retry + backoff | `notify_backend()` with exponential backoff | `orchestrator_agent.py` |
| Idempotent callbacks | Upsert on `(job_id, step_name)` | `api/agents.py` |
| Structured logging | `structlog` JSON logs | `main.py` |
| Request logging middleware | Duration + status on every request | `main.py` |
| Agent secret auth | `X-Agent-Secret` header validation | `api/agents.py` |
| Video upload retry | `@with_retry` decorator | `services/production.py` |
| Health check endpoint | `/health` with agent addresses | `main.py` |

---

## Scaling to 1000+ Users

1. **Replace in-memory rate limiter** → Redis with `fastapi-limiter`
2. **Replace in-memory job_state in Orchestrator** → Redis `ctx.storage` (Agentverse persistent storage)
3. **Add pgmq (Supabase)** for job queue instead of background tasks
4. **Deploy agents on Railway/Render** with auto-scaling workers
5. **Add Sentry** for error monitoring: `sentry_sdk.init(dsn=SENTRY_DSN)`
