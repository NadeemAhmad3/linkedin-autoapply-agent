"""Apply endpoints (Phase 4, review-and-confirm).

Starts a Playwright Easy-Apply session in a background thread. The session fills
the form and hands off to the human for the final Submit click.
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import DATA_DIR, PLAYWRIGHT_PROFILE_DIR, settings
from ..database import get_db
from ..models import Job
from ..schemas import ApplyProfile, ApplyStartRequest, ApplyStatus
from ..services import applier

router = APIRouter(prefix="/api/apply", tags=["apply"])

PROFILE_FILE = DATA_DIR / "apply_profile.json"


def _default_profile() -> dict:
    return {
        "email": settings.apply_email,
        "phone": settings.apply_phone,
        "resume_path": settings.apply_resume_path,
        "years_of_experience": settings.apply_years_of_experience,
        "education_level": settings.apply_education_level,
        "work_authorization": settings.apply_work_authorization,
        "require_sponsorship": settings.apply_require_sponsorship,
        "willing_to_relocate": settings.apply_willing_to_relocate,
        "notice_period": settings.apply_notice_period,
    }


def _load_profile() -> dict:
    if PROFILE_FILE.exists():
        try:
            data = json.loads(PROFILE_FILE.read_text())
            # Merge over defaults so new keys still appear.
            return {**_default_profile(), **data}
        except Exception:
            pass
    return _default_profile()


def _save_profile(data: dict) -> None:
    PROFILE_FILE.write_text(json.dumps(data, indent=2))


# --------------------------------------------------------------------------

@router.get("/profile", response_model=ApplyProfile)
def get_profile():
    return ApplyProfile(**_load_profile())


@router.put("/profile", response_model=ApplyProfile)
def put_profile(profile: ApplyProfile):
    _save_profile(profile.model_dump())
    return profile


@router.post("/start", response_model=ApplyStatus)
def start_apply(req: ApplyStartRequest, db: Session = Depends(get_db)):
    # Resolve the job URL.
    job_url = req.job_url
    if not job_url and req.job_id:
        job = db.get(Job, req.job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {req.job_id} not found")
        job_url = job.url
    if not job_url:
        raise HTTPException(status_code=400, detail="Provide job_id or job_url.")

    profile = (req.profile.model_dump() if req.profile else _load_profile())

    try:
        session = applier.start_apply(job_url, profile, str(PLAYWRIGHT_PROFILE_DIR))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ApplyStatus(**session.to_dict())


@router.get("/status", response_model=ApplyStatus)
def status(session_id: str):
    session = applier.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    return ApplyStatus(**session.to_dict())


@router.post("/cancel", response_model=ApplyStatus)
def cancel(session_id: str):
    session = applier.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    applier.cancel_apply(session_id)
    return ApplyStatus(**session.to_dict())
