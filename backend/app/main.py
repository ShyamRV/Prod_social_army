"""
AI Social Media Army — FastAPI Backend  (2026 edition)
Starts the database, mounts all routers, serves the API.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.session import create_tables
from app.api.agents import router as agents_router
from app.api.jobs import router as jobs_router
from app.api.auth import router as auth_router

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Creating database tables (if not exists)…")
    await create_tables()
    logger.info(f"{settings.APP_NAME} v{settings.APP_VERSION} ready ✅")
    yield
    logger.info("Shutting down backend…")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents_router)
app.include_router(jobs_router)
app.include_router(auth_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get("/")
async def root():
    return {
        "app": settings.APP_NAME,
        "docs": "/docs",
        "health": "/health",
    }