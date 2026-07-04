"""Resume upload + parsing endpoints."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import UPLOADS_DIR
from ..database import get_db
from ..models import Resume
from ..schemas import ResumeOut
from ..services import resume_parser

router = APIRouter(prefix="/api/resume", tags=["resume"])

ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf"}


@router.post("/upload", response_model=ResumeOut)
async def upload_resume(file: UploadFile = File(...), db: Session = Depends(get_db)):
    suffix = "." + (file.filename or "").rsplit(".", 1)[-1].lower() if file.filename else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {sorted(ALLOWED_SUFFIXES)}",
        )

    # Persist the upload so we can re-parse / debug later.
    saved = UPLOADS_DIR / f"{file.filename or 'resume'}"
    content = await file.read()
    saved.write_bytes(content)

    try:
        text = resume_parser.extract_text(saved)
        skills = resume_parser.extract_skills(text)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse resume: {exc}") from exc

    if not text:
        raise HTTPException(status_code=422, detail="No text could be extracted from the resume.")

    resume = Resume(filename=file.filename or "resume", raw_text=text, skills=",".join(skills))
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


@router.get("/{resume_id}", response_model=ResumeOut)
def get_resume(resume_id: int, db: Session = Depends(get_db)):
    resume = db.get(Resume, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    return resume
