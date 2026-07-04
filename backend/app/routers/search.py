"""Search endpoint: Apify discovery -> freshness filter -> match -> persist."""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Job, Match, Resume
from ..schemas import JobOut, SearchRequest, SearchResponse
from ..services import apify_client, matcher as matcher_module

router = APIRouter(prefix="/api", tags=["search"])


@router.post("/search", response_model=SearchResponse)
def search(req: SearchRequest, db: Session = Depends(get_db)):
    resume = db.get(Resume, req.resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail=f"Resume {req.resume_id} not found")

    token = (req.apify_token or settings.apify_token).strip()
    try:
        client = apify_client.ApifyClient(token=token, actor_id=settings.apify_actor_id)
        raw_items = client.search_jobs(
            keywords=req.keywords,
            location=req.location,
            limit=req.limit,
            easy_apply_only=req.easy_apply_only,
        )
    except apify_client.ApifyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Normalize + freshness filter
    freshness = req.freshness_minutes if req.freshness_minutes is not None else settings.search_freshness_minutes
    normalized = [apify_client.normalize_job(item) for item in raw_items]

    kept: list[dict] = []
    for nj in normalized:
        posted = apify_client.parse_posted_at(nj.get("posted_at_raw"))
        nj["posted_at"] = posted
        if apify_client.within(posted, freshness):
            kept.append(nj)

    # Match resume against kept jobs
    matcher = matcher_module.get_matcher(settings.match_model)
    skills = resume.skills.split(",") if resume.skills else []
    ranked = matcher.score_jobs(resume.raw_text, skills, kept)

    # Persist jobs + matches (dedupe by URL within this batch)
    out_jobs: list[JobOut] = []
    for rj in ranked:
        job = _upsert_job(db, rj)
        _upsert_match(db, resume.id, job.id, rj.get("score", 0.0), rj.get("skill_matches", []))
        out_jobs.append(_to_out(job, rj))

    return SearchResponse(
        resume_id=resume.id,
        total_found=len(raw_items),
        kept_after_freshness_filter=len(kept),
        jobs=out_jobs,
    )


def _upsert_job(db: Session, nj: dict) -> Job:
    url = nj.get("url") or ""
    existing = db.query(Job).filter_by(url=url).first() if url else None
    posted_at = nj.get("posted_at")
    # Store naive UTC for SQLite DATETIME columns.
    posted_naive = posted_at.replace(tzinfo=None) if isinstance(posted_at, datetime) else None

    fields = dict(
        title=nj.get("title") or "Untitled",
        company=nj.get("company") or "",
        location=nj.get("location") or "",
        url=url or f"unknown-{id(nj)}",
        description=nj.get("description") or "",
        posted_at=posted_naive,
        easy_apply=bool(nj.get("easy_apply")),
        raw_json=json.dumps(nj, default=str),
    )
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        db.flush()
        return existing
    job = Job(**fields)
    db.add(job)
    db.flush()
    return job


def _upsert_match(db: Session, resume_id: int, job_id: int, score: float, skill_matches: list) -> None:
    existing = db.query(Match).filter_by(resume_id=resume_id, job_id=job_id).first()
    skills_csv = ",".join(skill_matches)
    if existing:
        existing.score = score
        existing.skill_matches = skills_csv
    else:
        db.add(Match(resume_id=resume_id, job_id=job_id, score=score, skill_matches=skills_csv))
    db.flush()


def _to_out(job: Job, rj: dict) -> JobOut:
    return JobOut(
        id=job.id,
        title=job.title,
        company=job.company,
        location=job.location,
        url=job.url,
        description=job.description,
        posted_at=job.posted_at,
        easy_apply=job.easy_apply,
        score=float(rj.get("score", 0.0)),
        skill_matches=list(rj.get("skill_matches", [])),
    )
