"""Apify client: run a LinkedIn Jobs scraper actor and normalize results.

Discovery happens on Apify's infrastructure (their proxies, their sessions),
so this step never touches the user's LinkedIn account.

The exact field names returned depend on the actor configured by
``APIFY_ACTOR_ID``. ``normalize_job()`` maps the common variants; extend the
candidate lists there if you switch actors.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

RUN_SYNC_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}&timeout=300"


class ApifyError(RuntimeError):
    """Raised when an Apify run fails or returns an error payload."""


class ApifyClient:
    def __init__(self, token: str, actor_id: str):
        if not token:
            raise ApifyError(
                "No Apify token. Set APIFY_API_TOKEN in .env or pass apify_token."
            )
        self.token = token
        self.actor_id = actor_id

    def search_jobs(
        self,
        keywords: str,
        location: str,
        limit: int = 50,
        easy_apply_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Run the actor synchronously and return raw dataset items.

        Returns the raw list straight from Apify; normalization/freshness
        filtering is done by the caller via ``normalize_job`` / ``within``.
        """
        url = RUN_SYNC_URL.format(
            actor=quote(self.actor_id, safe=""),
            token=quote(self.token, safe=""),
        )
        payload = self._build_input(keywords, location, limit, easy_apply_only)
        logger.info("Calling Apify actor %s with input: %s", self.actor_id, payload)

        import httpx  # lazy: normalization/freshness logic below doesn't need it

        try:
            # run-sync blocks until the actor finishes (or times out at 300s).
            resp = httpx.post(url, json=payload, timeout=320)
        except httpx.HTTPError as exc:
            raise ApifyError(f"Apify request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise ApifyError(
                f"Apify returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        items = resp.json()
        if not isinstance(items, list):
            # Some errors come back as {"error": {...}}
            raise ApifyError(f"Unexpected Apify response: {str(items)[:500]}")
        logger.info("Apify returned %d items", len(items))
        return items

    def _build_input(
        self,
        keywords: str,
        location: str,
        limit: int,
        easy_apply_only: bool,
    ) -> dict[str, Any]:
        """Build the actor input.

        Targets the ``bebity/linkedin-jobs-scraper`` schema by default
        (``queries`` list + ``closeDuplicates`` + Easy Apply via filters).
        If you use another actor, adjust here or make it env-driven.
        """
        query = ", ".join(p for p in [keywords.strip(), location.strip()] if p)
        queries = [query] if query else ["software engineer"]
        payload: dict[str, Any] = {
            "queries": queries,
            "limit": limit,
            "closeDuplicates": True,
        }
        if easy_apply_only:
            # f_AL=true is LinkedIn's "Easy Apply" filter, supported by most actors.
            payload["filters"] = {"easyApply": True}
        return payload


# --------------------------------------------------------------------------
# Normalization + freshness filtering (actor-agnostic)
# --------------------------------------------------------------------------

def _first(record: dict[str, Any], keys: list[str], default: Any = "") -> Any:
    for k in keys:
        if k in record and record[k] not in (None, ""):
            return record[k]
    return default


def normalize_job(record: dict[str, Any]) -> dict[str, Any]:
    """Map a raw Apify record to our internal job dict."""
    url = str(_first(record, ["url", "link", "linkToJob", "jobUrl", "linkedinUrl"]))
    if url and url.startswith("/"):
        url = "https://www.linkedin.com" + url

    return {
        "title": str(_first(record, ["title", "jobTitle", "position"])).strip(),
        "company": str(_first(record, ["company", "companyName", "company_name"])).strip(),
        "location": str(_first(record, ["location", "jobLocation", "city", "formattedLocation"])).strip(),
        "url": url,
        "description": str(_first(record, ["description", "jobDescription", "descriptionText", "text"])).strip(),
        "posted_at_raw": _first(record, ["postedAt", "date", "datePosted", "listedAt", "postedTime", "timeAgo"], None),
        "easy_apply": bool(_first(record, ["easyApply", "isEasyApply", "applyMethod"], False)),
    }


def parse_posted_at(value: Any) -> datetime | None:
    """Best-effort parse of a job's posted timestamp.

    Handles ISO strings, epoch seconds/millis, and relative strings like
    '5 minutes ago'. Returns timezone-aware UTC, or None if unparseable.
    """
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            # epoch millis vs seconds heuristic
            ts = value / 1000 if abs(value) > 1e12 else value
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        s = str(value).strip()
        # epoch as string
        if s.isdigit():
            v = int(s)
            ts = v / 1000 if v > 1e12 else v
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        # ISO 8601 (handle trailing Z)
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return _parse_relative(s) if isinstance(value, str) else None


def _parse_relative(text: str) -> datetime | None:
    """Parse '5 minutes ago' / '2 hours ago' / '3 days ago' style strings."""
    import re

    m = re.search(r"(\d+)\s*(minute|min|hour|hr|day|week|month)s?\b", text.lower())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    secs = {
        "minute": 60, "min": 60,
        "hour": 3600, "hr": 3600,
        "day": 86400, "week": 604800, "month": 2592000,
    }[unit]
    return datetime.now(tz=timezone.utc) - timedelta(seconds=n * secs)


def within(posted_at: datetime | None, minutes: int, now: datetime | None = None) -> bool:
    """True if posted_at is within `minutes` of now (None -> exclude, to be safe)."""
    if posted_at is None:
        return False
    now = now or datetime.now(tz=timezone.utc)
    return (now - posted_at).total_seconds() <= minutes * 60
