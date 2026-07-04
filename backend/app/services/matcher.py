"""Resume <-> job matching.

Primary: sentence-transformers cosine similarity.
Fallback: token-overlap score, used automatically if the model can't load
(e.g. first-run download failed or torch is unavailable) so the API still works.
"""

from __future__ import annotations

import functools
import logging
import re
from typing import Any

import numpy as np

from .resume_parser import extract_skills

logger = logging.getLogger(__name__)

# Score weights (embedding similarity is the main signal; skills nudge it).
SKILL_BONUS_PER_MATCH = 3.0     # added points per matched skill
SKILL_BONUS_CAP = 12.0         # cap on the skill boost


class Matcher:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self._mode = "embeddings"  # or "fallback"
        self._load_model()

    def _load_model(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            # warm up so the first real request isn't slow
            self._model.encode("warmup", normalize_embeddings=True)
            logger.info("Loaded embedding model %s", self.model_name)
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.warning(
                "Could not load sentence-transformers model (%s). "
                "Falling back to token-overlap matching.", exc
            )
            self._model = None
            self._mode = "fallback"

    @property
    def mode(self) -> str:
        return self._mode

    def score_jobs(
        self,
        resume_text: str,
        resume_skills: list[str],
        jobs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return jobs annotated with ``score`` (0..100) and ``skill_matches``."""
        if not jobs:
            return []

        job_texts = [self._job_text(j) for j in jobs]

        if self._mode == "embeddings" and self._model is not None:
            base_scores = self._embedding_scores(resume_text, job_texts)
        else:
            base_scores = self._overlap_scores(resume_text, job_texts)

        results = []
        for job, base in zip(jobs, base_scores):
            matched = self._matched_skills(resume_skills, self._job_text(job))
            bonus = min(len(matched) * SKILL_BONUS_PER_MATCH, SKILL_BONUS_CAP)
            score = float(min(100.0, base + bonus))
            job_out = dict(job)
            job_out["score"] = round(score, 1)
            job_out["skill_matches"] = matched
            results.append(job_out)

        results.sort(key=lambda j: j["score"], reverse=True)
        return results

    # ----- signal computations -----

    def _job_text(self, job: dict[str, Any]) -> str:
        return " ".join(
            str(job.get(k, "") or "") for k in ("title", "company", "location", "description")
        ).strip()

    def _embedding_scores(self, resume_text: str, job_texts: list[str]) -> list[float]:
        """Cosine similarity (0..1) scaled to 0..100."""
        emb = self._model.encode(  # type: ignore[union-attr]
            [resume_text, *job_texts], normalize_embeddings=True, show_progress_bar=False
        )
        resume_vec = emb[0]
        job_vecs = emb[1:]
        sims = np.dot(job_vecs, resume_vec)  # already normalized -> cosine
        # similarities are roughly in [-0.1, 1]; map to a 0..100 scale
        scaled = np.clip(sims, 0.0, 1.0) * 100.0
        return scaled.tolist()

    def _overlap_scores(self, resume_text: str, job_texts: list[str]) -> list[float]:
        """Token Jaccard overlap scaled to 0..100 (fallback)."""
        resume_tokens = self._tokens(resume_text)
        if not resume_tokens:
            return [0.0] * len(job_texts)
        out = []
        for jt in job_texts:
            jt_tokens = self._tokens(jt)
            if not jt_tokens:
                out.append(0.0)
                continue
            inter = len(resume_tokens & jt_tokens)
            union = len(resume_tokens | jt_tokens)
            out.append((inter / union) * 100.0 if union else 0.0)
        return out

    def _matched_skills(self, resume_skills: list[str], text: str) -> list[str]:
        if not resume_skills or not text:
            return []
        low = text.lower()
        return [s for s in resume_skills if re.search(rf"(?<![a-z0-9]){re.escape(s.lower())}(?![a-z0-9])", low)]

    @staticmethod
    def _tokens(text: str) -> set[str]:
        stop = {
            "the", "and", "for", "with", "you", "our", "are", "will", "this",
            "that", "have", "from", "your", "into", "their", "they", "but",
            "not", "all", "can", "who", "we", "is", "to", "of", "in", "a", "as",
            "on", "or", "an", "be", "at", "by", "it",
        }
        return {w for w in re.findall(r"[a-z0-9+#.]{2,}", text.lower()) if w not in stop}


@functools.lru_cache(maxsize=1)
def get_matcher(model_name: str) -> Matcher:
    """Process-wide cached Matcher (model loads once)."""
    return Matcher(model_name)
