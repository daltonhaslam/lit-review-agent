"""CrossRef API client.

Implements:
  - resolve_doi(doi) → Article | None
  - get_references(doi) → list[str]  (DOIs of cited works)
  - search_by_title(title, author, year) → str | None  (DOI for DOI resolution)
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

import httpx

from ..models.article import Article
from ..utils.rate_limiter import LIMITERS, RateLimiter

BASE_URL = "https://api.crossref.org"
LIMITER = LIMITERS["api.crossref.org"]


class CrossRefClient:
    def __init__(self, mailto: str = ""):
        self._mailto = mailto

    def _params(self, extra: dict | None = None) -> dict:
        params = {}
        if self._mailto:
            params["mailto"] = self._mailto
        if extra:
            params.update(extra)
        return params

    async def _get(self, path: str, params: dict | None = None) -> dict:
        await LIMITER.acquire()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{BASE_URL}{path}", params=self._params(params or {})
            )
            resp.raise_for_status()
            return resp.json()

    async def resolve_doi(self, doi: str) -> Optional[Article]:
        """Fetch metadata for a single DOI."""
        try:
            data = await RateLimiter.retry_with_backoff(
                lambda: self._get(f"/works/{quote(doi, safe='')}")
            )
            return _parse_crossref_work(data.get("message") or {})
        except Exception:
            return None

    async def search_by_title(
        self,
        title: str,
        first_author_family: str = "",
        year: int | None = None,
    ) -> str | None:
        """Search CrossRef by title/author/year; return the best-match DOI or None."""
        params: dict = {"rows": "1", "select": "DOI,title,author,score"}
        params["query.title"] = title[:200]
        if first_author_family:
            params["query.author"] = first_author_family
        if year:
            params["filter"] = f"from-pub-date:{year},until-pub-date:{year}"

        try:
            data = await RateLimiter.retry_with_backoff(
                lambda: self._get("/works", params)
            )
            items = (data.get("message") or {}).get("items") or []
            if items and items[0].get("score", 0) > 80:
                return (items[0].get("DOI") or "").lower() or None
        except Exception:
            pass
        return None

    async def get_references(self, doi: str) -> list[str]:
        """Return DOIs of works cited by this paper (backward citation chasing)."""
        try:
            data = await RateLimiter.retry_with_backoff(
                lambda: self._get(f"/works/{quote(doi, safe='')}")
            )
            refs = data.get("message", {}).get("reference") or []
            return [
                r["DOI"].lower()
                for r in refs
                if isinstance(r, dict) and r.get("DOI")
            ]
        except Exception:
            return []


def _parse_crossref_work(work: dict) -> Optional[Article]:
    try:
        title_list = work.get("title") or []
        title = title_list[0] if title_list else ""
        if not title:
            return None

        doi = (work.get("DOI") or "").lower() or None

        authors = []
        for author in work.get("author") or []:
            family = author.get("family", "")
            given = author.get("given", "")
            name = f"{family} {given}".strip()
            if name:
                authors.append(name)

        year = None
        for date_field in ("published-print", "published-online", "created"):
            date_parts = work.get(date_field, {}).get("date-parts")
            if date_parts and date_parts[0]:
                try:
                    year = int(date_parts[0][0])
                    break
                except (TypeError, ValueError, IndexError):
                    pass

        journal_list = work.get("container-title") or []
        journal = journal_list[0] if journal_list else None

        volume = work.get("volume")
        issue = work.get("issue")
        pages = work.get("page")

        return Article(
            doi=doi,
            title=title,
            authors=authors,
            year=year,
            journal=journal,
            volume=volume,
            issue=issue,
            pages=pages,
            source_dbs=["crossref"],
        )
    except Exception:
        return None
