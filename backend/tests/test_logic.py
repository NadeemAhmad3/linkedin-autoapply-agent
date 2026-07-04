"""Tests for the pure-Python logic (no network, no heavy deps).

These run with zero pip installs for resume_parser + apify_client normalization.
The matcher test needs numpy; it's skipped automatically if unavailable.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running from anywhere by adding the backend root to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import apify_client, resume_parser  # noqa: E402


# ---------- resume_parser ----------

def test_extract_skills_finds_known_skills():
    text = "I build APIs in Python and deploy with Docker on AWS. Also some React."
    skills = resume_parser.extract_skills(text)
    assert "python" in skills
    assert "docker" in skills
    assert "aws" in skills
    assert "react" in skills


def test_extract_skills_respects_word_boundary():
    # 'java' must not match inside 'javascript'
    skills = resume_parser.extract_skills("I love javascript and java")
    assert "javascript" in skills
    assert "java" in skills


def test_extract_text_from_sample():
    text = resume_parser.extract_text(Path(__file__).resolve().parent.parent / "sample_resume.txt")
    assert "Jane Developer" in text
    assert "FastAPI" in text


# ---------- apify_client normalization ----------

def test_normalize_job_maps_variants():
    rec = {
        "jobTitle": "Backend Engineer",
        "companyName": "Acme",
        "location": "Remote",
        "link": "/jobs/view/123",
        "description": "Build APIs",
        "postedAt": "2024-01-01T00:00:00Z",
        "easyApply": True,
    }
    nj = apify_client.normalize_job(rec)
    assert nj["title"] == "Backend Engineer"
    assert nj["company"] == "Acme"
    assert nj["url"] == "https://www.linkedin.com/jobs/view/123"
    assert nj["easy_apply"] is True


def test_parse_posted_at_iso_and_relative():
    iso = apify_client.parse_posted_at("2024-01-01T12:00:00Z")
    assert iso == datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

    rel = apify_client.parse_posted_at("10 minutes ago")
    assert rel is not None
    delta = datetime.now(tz=timezone.utc) - rel
    assert timedelta(seconds=590) < delta < timedelta(seconds=650)


def test_within_filters_by_minutes():
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    recent = now - timedelta(minutes=20)
    old = now - timedelta(minutes=120)
    assert apify_client.within(recent, 30, now=now) is True
    assert apify_client.within(old, 30, now=now) is False
    assert apify_client.within(None, 30, now=now) is False  # unknown age excluded


# ---------- matcher (skipped if numpy/torch unavailable) ----------

def test_matcher_ranks_relevant_job_higher():
    try:
        import numpy  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("numpy not installed")

    from app.services import matcher as matcher_module

    # Force the keyword-overlap path so the test doesn't depend on torch/model.
    m = matcher_module.Matcher.__new__(matcher_module.Matcher)
    m._mode = "fallback"
    m._model = None

    resume = "Senior Python backend engineer with FastAPI, PostgreSQL, Docker, AWS."
    skills = ["python", "fastapi", "postgresql", "docker", "aws"]
    jobs = [
        {"title": "Frontend Developer", "company": "X", "location": "", "description": "React, CSS, HTML"},
        {"title": "Senior Backend Engineer", "company": "Y", "location": "", "description": "Python, FastAPI, PostgreSQL, Docker, AWS"},
    ]
    ranked = m.score_jobs(resume, skills, jobs)
    assert ranked[0]["title"] == "Senior Backend Engineer"
    assert ranked[0]["score"] > ranked[1]["score"]
    assert "python" in ranked[0]["skill_matches"]


# ---------- applier form brain ----------

def test_answer_for_matches_common_questions():
    try:
        import playwright  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("playwright not installed")
    from app.services.applier import _answer_for

    profile = {"years_of_experience": "7", "work_authorization": "Yes",
               "require_sponsorship": "No", "education_level": "Master's Degree"}
    assert _answer_for("How many years of experience do you have?", profile) == "7"
    assert _answer_for("Are you legally authorized to work in the US?", profile) == "Yes"
    assert _answer_for("Will you now or in the future require sponsorship?", profile) == "No"
    assert _answer_for("What is your highest level of education?", profile) == "Master's Degree"
    assert _answer_for("What is your favorite color?", profile) is None  # unknown question


if __name__ == "__main__":
    # Quick standalone run without pytest.
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
