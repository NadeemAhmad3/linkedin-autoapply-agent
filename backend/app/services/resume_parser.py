"""Parse uploaded resumes (PDF / DOCX / TXT) into raw text + skills."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


# A pragmatic, extendable skills dictionary. Not exhaustive — good enough to
# power a "skill overlap" boost on top of the embedding similarity score.
SKILL_CATALOG: list[str] = [
    # Languages
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "golang",
    "rust", "ruby", "php", "swift", "kotlin", "scala", "r", "sql", "bash", "shell",
    # Frontend
    "react", "next.js", "nextjs", "vue", "vue.js", "angular", "svelte", "html",
    "css", "tailwind", "sass", "redux", "webpack", "vite",
    # Backend / frameworks
    "django", "flask", "fastapi", "node.js", "express", "spring", "spring boot",
    "dotnet", ".net", "laravel", "rails", "graphql", "rest api", "grpc",
    # Data / ML
    "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch", "keras", "nlp",
    "machine learning", "deep learning", "data analysis", "data engineering",
    "spark", "hadoop", "airflow", "dbt", "tableau", "power bi", "etl", "llm",
    # Cloud / DevOps
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform", "ansible",
    "jenkins", "ci/cd", "linux", "github actions", "cloudformation",
    # Databases
    "postgresql", "postgres", "mysql", "mongodb", "redis", "elasticsearch",
    "dynamodb", "snowflake", "bigquery", "cassandra",
    # Practices / soft
    "agile", "scrum", "kanban", "tdd", "unit testing", "microservices",
    "system design", "leadership", "communication", "project management",
]


def _extract_text_from_pdf(path: Path) -> str:
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def _extract_text_from_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs).strip()


def _extract_text_from_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def extract_text(path: Path) -> str:
    """Dispatch to the right parser by extension."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_text_from_pdf(path)
    if suffix in {".docx", ".doc"}:
        return _extract_text_from_docx(path)
    if suffix in {".txt", ".md", ".rtf"}:
        return _extract_text_from_txt(path)
    # Last-ditch: try as text.
    logger.warning("Unknown resume extension %s; attempting plain-text read", suffix)
    return _extract_text_from_txt(path)


def extract_skills(text: str) -> list[str]:
    """Find catalog skills present in the text (case-insensitive, word-ish bound)."""
    if not text:
        return []
    haystack = text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for skill in SKILL_CATALOG:
        needle = re.escape(skill.lower())
        # Word boundary-ish; allow "." and "/" which appear in skill names.
        pattern = rf"(?<![a-z0-9]){needle}(?![a-z0-9])"
        if re.search(pattern, haystack) and skill not in seen:
            found.append(skill)
            seen.add(skill)
    return found
