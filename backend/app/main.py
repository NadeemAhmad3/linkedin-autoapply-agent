"""FastAPI application entrypoint."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import FRONTEND_DIR, settings
from .database import init_db
from .routers import apply, jobs, resume, search

# Create tables at import time so the app works under any runner
# (uvicorn, gunicorn, TestClient) — create_all is idempotent.
init_db()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="LinkedIn Job Finder",
    description="Resume -> Apify job discovery -> embeddings match -> (later) review-and-confirm apply.",
    version="0.1.0",
)

# CORS for the future frontend (dev servers run on a different port).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "apify_configured": bool(settings.apify_token),
        "actor": settings.apify_actor_id,
        "freshness_minutes": settings.search_freshness_minutes,
    }


@app.get("/", include_in_schema=False)
def root():
    """Serve the single-page frontend dashboard."""
    return FileResponse(FRONTEND_DIR / "index.html")


app.include_router(resume.router)
app.include_router(search.router)
app.include_router(jobs.router)
app.include_router(apply.router)
