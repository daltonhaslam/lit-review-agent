"""Semantic Scholar API client.

Implements:
  - search(query, max_results) → list[Article]
  - batch_lookup(paper_ids) → list[Article]
  - get_citations(paper_id) → list[str]  (S2 paper IDs citing this paper)
"""
from __future__ import annotations

import asyncio
from typing import Optional

import httpx
from rapidfuzz import fuzz

from ..models.article import Article


def _reverse_name(name: str) -> str:
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[-1]} {' '.join(parts[:-1])}"
    return name
from ..utils.rate_limiter import LIMITERS, RateLimiter

BASE_URL = "https://api.semanticscholar.org/graph/v1"
LIMITER = LIMITERS["api.semanticscholar.org"]

PAPER_FIELDS = (
    "paperId,externalIds,title,abstract,authors,year,venue,"
    "citationCount,influentialCitationCount,tldr,isOpenAccess,openAccessPdf"
)


class SemanticScholarClient:
    def __init__(self, api_key: str = ""):
        self._api_key = api_key

    def _headers(self) -> dict:
        if self._api_key:
            return {"x-api-key": self._api_key}
        return {}

    async def _get(self, path: str, params: dict) -> dict:
        await LIMITER.acquire()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{BASE_URL}{path}", params=params, headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str, params: dict, body: dict) -> list:
        await LIMITER.acquire()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{BASE_URL}{path}",
                params=params,
                json=body,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def search(
        self,
        query: str,
        max_results: int = 200,
        year_range: tuple[int, int] | None = None,
    ) -> list[Article]:
        """Search papers by keyword query."""
        limit = min(max_results, 100)
        articles: list[Article] = []
        offset = 0
        year_param = f"{year_range[0]}-{year_range[1]}" if year_range else None

        while len(articles) < max_results:
            try:
                data = await RateLimiter.retry_with_backoff(
                    lambda o=offset: self._get(
                        "/paper/search",
                        {
                            "query": query,
                            "fields": PAPER_FIELDS,
                            "limit": limit,
                            "offset": o,
                            **({"year": year_param} if year_param else {}),
                        },
                    )
                )
            except Exception:
                break

            for paper in data.get("data") or []:
                article = _parse_s2_paper(paper)
                if article:
                    articles.append(article)

            total = data.get("total", 0)
            offset += limit
            if offset >= total or not data.get("data"):
                break
            await asyncio.sleep(1.2)  # S2 rate limit is stricter than others

        return articles[:max_results]

    async def batch_lookup(self, paper_ids: list[str]) -> list[Article]:
        """Enrich up to 500 papers per call using the batch endpoint.

        paper_ids may be PMID:12345, DOI:10.xxx/..., or bare S2 paper IDs.
        """
        if not paper_ids:
            return []

        articles: list[Article] = []
        batch_size = 100
        for i in range(0, len(paper_ids), batch_size):
            batch = paper_ids[i : i + batch_size]
            try:
                results = await RateLimiter.retry_with_backoff(
                    lambda b=batch: self._post(
                        "/paper/batch",
                        {"fields": PAPER_FIELDS},
                        {"ids": b},
                    )
                )
                for paper in results or []:
                    if paper:
                        article = _parse_s2_paper(paper)
                        if article:
                            articles.append(article)
            except Exception:
                pass
            await asyncio.sleep(1.2)

        return articles

    async def search_paper(self, title: str, year: Optional[int] = None) -> Optional[Article]:
        """Search for a single paper by title; return Article if similarity ≥ 0.85."""
        year_range = (year, year) if year else None
        try:
            results = await self.search(title, max_results=3, year_range=year_range)
        except Exception:
            return None
        norm = title.lower().strip()
        for r in results:
            if fuzz.ratio((r.title or "").lower().strip(), norm) >= 85:
                return r
        return None

    async def get_citations(self, paper_id: str, limit: int = 200) -> list[str]:
        """Return S2 paper IDs of works citing this paper (forward citation chasing)."""
        try:
            data = await RateLimiter.retry_with_backoff(
                lambda: self._get(
                    f"/paper/{paper_id}/citations",
                    {"fields": "paperId,externalIds,title", "limit": min(limit, 1000)},
                )
            )
            return [
                c["citingPaper"]["paperId"]
                for c in (data.get("data") or [])
                if c.get("citingPaper", {}).get("paperId")
            ]
        except Exception:
            return []


def _parse_s2_paper(paper: dict) -> Optional[Article]:
    try:
        title = paper.get("title") or ""
        if not title:
            return None

        ext = paper.get("externalIds") or {}
        doi = (ext.get("DOI") or "").lower() or None
        pmid = str(ext["PubMed"]) if ext.get("PubMed") else None
        s2_id = paper.get("paperId")

        authors = [
            _reverse_name(a["name"])
            for a in (paper.get("authors") or [])
            if a.get("name")
        ]

        year = paper.get("year")
        journal = paper.get("venue") or None
        abstract = paper.get("abstract") or None

        tldr_obj = paper.get("tldr") or {}
        tldr = tldr_obj.get("text") if isinstance(tldr_obj, dict) else None

        oa_pdf = paper.get("openAccessPdf") or {}
        oa_url = oa_pdf.get("url") if isinstance(oa_pdf, dict) else None

        return Article(
            pmid=pmid,
            doi=doi,
            s2_id=s2_id,
            title=title,
            abstract=abstract,
            authors=authors,
            year=year,
            journal=journal,
            citation_count=paper.get("citationCount"),
            influential_citation_count=paper.get("influentialCitationCount"),
            tldr=tldr,
            oa_status=paper.get("isOpenAccess"),
            oa_pdf_url=oa_url,
            source_dbs=["semantic_scholar"],
        )
    except Exception:
        return None
