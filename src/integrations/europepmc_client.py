"""Europe PMC API client.

Implements:
  - search(query, filters) → list[Article]
  - get_fulltext(pmid_or_doi) → str | None  (full-text XML when available)
"""
from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from ..models.article import Article
from ..utils.rate_limiter import LIMITERS, RateLimiter

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
LIMITER = LIMITERS["www.ebi.ac.uk"]


class EuropePMCClient:
    async def _request(self, path: str, params: dict) -> httpx.Response:
        await LIMITER.acquire()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{BASE_URL}{path}", params=params)
            resp.raise_for_status()
            return resp

    async def _get(self, path: str, params: dict) -> dict:
        return (await self._request(path, params)).json()

    async def _get_text(self, path: str, params: dict) -> str:
        return (await self._request(path, params)).text

    async def search(
        self,
        query: str,
        filters: Optional[list[str]] = None,
        max_results: int = 500,
    ) -> list[Article]:
        """Search Europe PMC. Filters are appended as AND clauses to the query."""
        full_query = query
        if filters:
            full_query += " AND " + " AND ".join(f"({f})" for f in filters)

        articles: list[Article] = []
        cursor_mark = "*"
        page_size = min(max_results, 200)

        while len(articles) < max_results:
            try:
                data = await RateLimiter.retry_with_backoff(
                    lambda cm=cursor_mark: self._get(
                        "/search",
                        {
                            "query": full_query,
                            "format": "json",
                            "resultType": "core",
                            "pageSize": page_size,
                            "cursorMark": cm,
                        },
                    )
                )
            except Exception:
                break

            results = (data.get("resultList") or {}).get("result") or []
            for r in results:
                article = _parse_europepmc_result(r)
                if article:
                    articles.append(article)

            next_cursor = data.get("nextCursorMark")
            if not next_cursor or next_cursor == cursor_mark or not results:
                break
            cursor_mark = next_cursor
            await asyncio.sleep(0.1)

        return articles[:max_results]

    async def get_fulltext(self, pmid_or_doi: str) -> Optional[str]:
        """Fetch full-text XML for a paper (available for OA papers only)."""
        if pmid_or_doi.startswith("10."):
            path = f"/doi/{pmid_or_doi}/fullTextXML"
        else:
            path = f"/{pmid_or_doi}/fullTextXML"

        try:
            return await RateLimiter.retry_with_backoff(
                lambda: self._get_text(path, {})
            )
        except Exception:
            return None


def _parse_europepmc_result(result: dict) -> Optional[Article]:
    try:
        title = result.get("title") or ""
        if not title:
            return None

        pmid = str(result["pmid"]) if result.get("pmid") else None
        doi = (result.get("doi") or "").lower() or None

        authors = []
        author_list = (result.get("authorList") or {}).get("author") or []
        for author in author_list:
            last = author.get("lastName", "")
            first = author.get("firstName", "")
            if last:
                full = f"{last} {first}".strip()
            else:
                full = author.get("fullName") or ""
            if full:
                authors.append(full)

        year_raw = result.get("pubYear")
        try:
            year = int(year_raw) if year_raw else None
        except (ValueError, TypeError):
            year = None

        journal = result.get("journalTitle") or result.get("journalInfo", {}).get("journal", {}).get("title")
        abstract = result.get("abstractText") or None
        citation_count = result.get("citedByCount")

        oa_status = result.get("isOpenAccess") == "Y"
        oa_url = result.get("fullTextUrlList", {})
        if isinstance(oa_url, dict):
            urls = oa_url.get("fullTextUrl") or []
            pdf_url = next(
                (u.get("url") for u in urls if u.get("documentStyle") == "pdf"),
                None,
            )
        else:
            pdf_url = None

        return Article(
            pmid=pmid,
            doi=doi,
            title=title,
            abstract=abstract,
            authors=authors,
            year=year,
            journal=journal,
            citation_count=citation_count,
            oa_status=oa_status,
            oa_pdf_url=pdf_url,
            source_dbs=["europepmc"],
        )
    except Exception:
        return None
