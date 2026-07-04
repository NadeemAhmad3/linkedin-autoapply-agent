"""Job listing / detail endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Job, Match
from ..schemas import JobOut

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _job_to_out(job: Job, match: Match | None = None) -> JobOut:
    return JobOut(
        id=job.id,
        title=job.title,
        company=job.company,
        location=job.location,
        url=job.url,
        description=job.description,
        posted_at=job.posted_at,
        easy_apply=job.easy_apply,
        score=match.score if match else None,
        skill_matches=match.skill_matches.split(",") if (match and match.skill_matches) else [],
    )


@router.get("", response_model=list[JobOut])
def list_jobs(
    resume_id: int | None = Query(None, description="If given, attach fit score for this resume."),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(Job).order_by(Job.created_at.desc()).limit(limit)
    jobs = query.all()
    out = []
    for j in jobs:
        match = None
        if resume_id:
            match = db.query(Match).filter_by(resume_id=resume_id, job_id=j.id).first()
        out.append(_job_to_out(j, match))
    return out


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, resume_id: int | None = None, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    match = None
    if resume_id:
        match = db.query(Match).filter_by(resume_id=resume_id, job_id=job.id).first()
    return _job_to_out(job, match)
