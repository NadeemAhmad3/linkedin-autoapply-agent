"""Pydantic request/response schemas."""

from datetime import datetime
from pydantic import BaseModel, Field, field_validator


# ---------- Resume ----------

class ResumeOut(BaseModel):
    id: int
    filename: str
    raw_text: str
    skills: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("skills", mode="before")
    @classmethod
    def _split_skills(cls, v):
        # ORM stores skills as a comma-separated string; accept list or string.
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v or []


# ---------- Jobs / search ----------

class SearchRequest(BaseModel):
    resume_id: int
    keywords: str = Field("", description="Job search keywords, e.g. 'Python Developer'")
    location: str = Field("", description="Location, e.g. 'Toronto'")
    freshness_minutes: int | None = Field(
        None, description="Override freshness window (minutes). Default from settings."
    )
    easy_apply_only: bool = Field(False, description="Restrict to Easy Apply jobs.")
    limit: int = Field(50, ge=1, le=200, description="Max jobs to request from Apify.")
    apify_token: str | None = Field(
        None, description="Override APIFY_API_TOKEN for this request."
    )


class JobOut(BaseModel):
    id: int
    title: str
    company: str
    location: str
    url: str
    description: str
    posted_at: datetime | None
    easy_apply: bool
    # present when results come from /search
    score: float | None = None
    skill_matches: list[str] | None = None

    model_config = {"from_attributes": True}


class SearchResponse(BaseModel):
    resume_id: int
    total_found: int
    kept_after_freshness_filter: int
    jobs: list[JobOut]


# ---------- Apply (Phase 4) ----------

class ApplyProfile(BaseModel):
    email: str = ""
    phone: str = ""
    resume_path: str = ""
    years_of_experience: str = "5"
    education_level: str = "Bachelor's Degree"
    work_authorization: str = "Yes"
    require_sponsorship: str = "No"
    willing_to_relocate: str = "Yes"
    notice_period: str = "15"


class ApplyStartRequest(BaseModel):
    job_id: int | None = None
    job_url: str | None = None
    profile: ApplyProfile | None = None


class ApplyStatus(BaseModel):
    session_id: str
    status: str
    message: str
    job_url: str
    elapsed_sec: float
