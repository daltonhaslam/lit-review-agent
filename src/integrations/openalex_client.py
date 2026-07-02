"""OpenAlex API client.

Implements:
  - search(query, filters, year_range) → list[Article]
  - get_work(doi) → Article | None
"""
from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import quote

import httpx

from ..models.article import Article


def _reverse_name(name: str) -> str:
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[-1]} {' '.join(parts[:-1])}"
    return name
from ..utils.rate_limiter import LIMITERS, RateLimiter

BASE_URL = "https://api.openalex.org"
LIMITER = LIMITERS["api.openalex.org"]

WORK_FIELDS = (
    "id,doi,title,abstract_inverted_index,authorships,publication_year,"
    "primary_location,cited_by_count,concepts,open_access,ids,best_oa_location"
)


class OpenAlexClient:
    def __init__(self, email: str = ""):
        self._email = email

    def _params(self, extra: dict | None = None) -> dict:
        params = {"select": WORK_FIELDS}
        if self._email:
            params["mailto"] = self._email
        if extra:
            params.update(extra)
        return params

    async def _get(self, path: str, params: dict) -> dict:
        await LIMITER.acquire()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{BASE_URL}{path}", params=params)
            resp.raise_for_status()
            return resp.json()

    async def search(
        self,
        query: str,
        filters: Optional[list[str]] = None,
        year_range: Optional[tuple[int, int]] = None,
        max_results: int = 500,
    ) -> list[Article]:
        """Full-text search of OpenAlex works."""
        filter_parts: list[str] = []
        if year_range:
            filter_parts.append(f"publication_year:{year_range[0]}-{year_range[1]}")
        if filters:
            filter_parts.extend(filters)

        extra: dict = {"search": query, "per-page": min(max_results, 200)}
        if filter_parts:
            extra["filter"] = ",".join(filter_parts)

        articles: list[Article] = []
        cursor = "*"
        while len(articles) < max_results:
            extra["cursor"] = cursor
            try:
                data = await RateLimiter.retry_with_backoff(
                    lambda: self._get("/works", self._params(extra))
                )
            except Exception:
                break

            results = data.get("results", [])
            for work in results:
                article = _parse_openalex_work(work)
                if article:
                    articles.append(article)

            next_cursor = data.get("meta", {}).get("next_cursor")
            if not next_cursor or not results:
                break
            cursor = next_cursor
            await asyncio.sleep(0.1)

        return articles[:max_results]

    async def get_work(self, doi: str) -> Optional[Article]:
        """Fetch a single work by DOI."""
        try:
            data = await RateLimiter.retry_with_backoff(
                lambda: self._get(f"/works/doi:{quote(doi, safe='')}", self._params())
            )
            return _parse_openalex_work(data)
        except Exception:
            return None


def _reconstruct_abstract(inv_index: dict | None) -> Optional[str]:
    if not inv_index:
        return None
    try:
        max_pos = max(pos for positions in inv_index.values() for pos in positions)
        words = [""] * (max_pos + 1)
        for word, positions in inv_index.items():
            for pos in positions:
                if 0 <= pos <= max_pos:
                    words[pos] = word
        return " ".join(w for w in words if w)
    except (ValueError, TypeError):
        return None


def _parse_openalex_work(work: dict) -> Optional[Article]:
    try:
        title = work.get("title") or ""
        if not title:
            return None

        doi = (work.get("doi") or "").replace("https://doi.org/", "").lower() or None
        pmid = None
        ids = work.get("ids") or {}
        if "pmid" in ids:
            raw_pmid = ids["pmid"] or ""
            pmid = raw_pmid.replace("https://pubmed.ncbi.nlm.nih.gov/", "").strip("/") or None

        authors = []
        for authorship in work.get("authorships") or []:
            author = authorship.get("author") or {}
            name = author.get("display_name") or ""
            if name:
                authors.append(_reverse_name(name))

        year = work.get("publication_year")

        journal = None
        primary_loc = work.get("primary_location") or {}
        source = primary_loc.get("source") or {}
        if source:
            journal = source.get("display_name")

        abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))

        concepts = [
            c.get("display_name", "")
            for c in (work.get("concepts") or [])
            if c.get("score", 0) >= 0.3
        ]

        oa = work.get("open_access") or {}
        oa_status = oa.get("is_oa")
        oa_url = oa.get("oa_url")
        if not oa_url:
            best_oa = work.get("best_oa_location") or {}
            oa_url = best_oa.get("pdf_url") or best_oa.get("landing_page_url")

        openalex_id = work.get("id", "")

        return Article(
            pmid=pmid,
            doi=doi,
            openalex_id=openalex_id,
            title=title,
            abstract=abstract,
            authors=authors,
            year=year,
            journal=journal,
            concepts=concepts,
            citation_count=work.get("cited_by_count"),
            oa_status=oa_status,
            oa_pdf_url=oa_url,
            source_dbs=["openalex"],
        )
    except Exception:
        return None
