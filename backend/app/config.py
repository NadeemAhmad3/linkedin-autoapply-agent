"""Application configuration loaded from environment / .env."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolve paths relative to this file so they work regardless of CWD.
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"
PLAYWRIGHT_PROFILE_DIR = DATA_DIR / "playwright_profile"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    """Runtime settings. Override via environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Apify
    apify_api_token: str = ""
    apify_actor_id: str = "bebity/linkedin-jobs-scraper"

    # Search
    search_freshness_minutes: int = 30

    # Matching
    match_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Storage
    database_url: str = f"sqlite:///{DATA_DIR / 'app.db'}"

    # Apply (Phase 4) — profile defaults. Override per-request or via the UI.
    apply_email: str = ""
    apply_phone: str = ""
    apply_resume_path: str = ""
    apply_years_of_experience: str = "5"
    apply_education_level: str = "Bachelor's Degree"
    apply_work_authorization: str = "Yes"
    apply_require_sponsorship: str = "No"
    apply_willing_to_relocate: str = "Yes"
    apply_notice_period: str = "15"

    @property
    def apify_token(self) -> str:
        """Token resolved from settings (or empty if not configured)."""
        return self.apify_api_token.strip()


settings = Settings()
