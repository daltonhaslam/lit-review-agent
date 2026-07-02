"""Enrichment of included articles via Semantic Scholar and OpenAlex.

Fetches citation counts, citation velocity, TLDRs, and topic/concept tags
for included articles. Does not call the LLM.

Enrichment applied:
  - Semantic Scholar batch_lookup → citation_count, citation_velocity,
    influential_citation_count, TLDR, OA PDF URL (if not already set)
  - OpenAlex get_work → concepts

citation_velocity = citation_count / max(1, current_year - pub_year)

Input:  review_dir (reads articles_included.json; status must be data_extracted)
Output: articles_included.json updated with enrichment fields; status=enriched
CLI:    python -m src.skills.enricher --review-dir reviews/{slug}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.integrations.config import load_api_config
from src.integrations.openalex_client import OpenAlexClient
from src.integrations.semantic_scholar_client import SemanticScholarClient
from src.models.article import Article
from src.models.review import ReviewStatus

_CURRENT_YEAR = datetime.now(timezone.utc).year


def _build_s2_ids(articles: list[Article]) -> list[str | None]:
    ids: list[str | None] = []
    for a in articles:
        if a.pmid:
            ids.append(f"PMID:{a.pmid}")
        elif a.doi:
            ids.append(f"DOI:{a.doi}")
        elif a.s2_id:
            ids.append(a.s2_id)
        else:
            ids.append(None)
    return ids


def _compute_velocity(citation_count: int | None, pub_year: int | None) -> float | None:
    if citation_count is None or pub_year is None:
        return None
    years = max(1, _CURRENT_YEAR - pub_year)
    return round(citation_count / years, 2)


def _apply_s2_enrichment(article: Article, enriched: Article) -> None:
    if enriched.citation_count is not None:
        article.citation_count = enriched.citation_count
    if enriched.influential_citation_count is not None:
        article.influential_citation_count = enriched.influential_citation_count
    if enriched.tldr and not article.tldr:
        article.tldr = enriched.tldr
    if enriched.oa_pdf_url and not article.oa_pdf_url:
        article.oa_pdf_url = enriched.oa_pdf_url
    if enriched.s2_id and not article.s2_id:
        article.s2_id = enriched.s2_id
    article.citation_velocity = _compute_velocity(article.citation_count, article.year)


async def _enrich_openalex(client: OpenAlexClient, articles: list[Article]) -> None:
    doi_articles = [a for a in articles if a.doi]
    if not doi_articles:
        return

    async def _fetch_one(article: Article) -> None:
        try:
            work = await client.get_work(article.doi)
            if work and work.concepts:
                article.concepts = work.concepts
        except Exception:
            pass

    await asyncio.gather(*[_fetch_one(a) for a in doi_articles])


async def enrich(review_dir: Path) -> dict:
    review = state_manager.load(review_dir)
    if review.status != ReviewStatus.data_extracted:
        raise ValueError(f"Expected status 'data_extracted', got '{review.status}'.")

    included_path = review_dir / review.articles_included_file
    articles = [Article(**a) for a in json.loads(included_path.read_text())]

    if not articles:
        review.status = ReviewStatus.enriched
        state_manager.save(review, review_dir)
        return {"enriched": 0, "s2_matched": 0, "openalex_matched": 0}

    cfg = load_api_config()
    s2_client = SemanticScholarClient(api_key=cfg.semantic_scholar_api_key)
    oa_client = OpenAlexClient(email=cfg.openalex_email)

    s2_ids = _build_s2_ids(articles)
    valid_ids = [sid for sid in s2_ids if sid is not None]

    s2_matched = 0
    if valid_ids:
        enriched_s2 = await s2_client.batch_lookup(valid_ids)
        by_pmid: dict[str, Article] = {}
        by_doi: dict[str, Article] = {}
        by_s2: dict[str, Article] = {}
        for e in enriched_s2:
            if e.pmid:
                by_pmid[e.pmid] = e
            if e.doi:
                by_doi[e.doi.lower()] = e
            # Forward-chased records may have only an s2_id (paperId); key on it
            # so match-back can find them when pmid/doi are both None.
            if e.s2_id:
                by_s2[e.s2_id] = e

        for article in articles:
            match = None
            # Mirror the pmid → doi → s2_id priority used in _build_s2_ids so a
            # bare-s2_id article (doi=None, pmid=None) still gets enriched.
            if article.pmid and article.pmid in by_pmid:
                match = by_pmid[article.pmid]
            elif article.doi and article.doi.lower() in by_doi:
                match = by_doi[article.doi.lower()]
            elif article.s2_id and article.s2_id in by_s2:
                match = by_s2[article.s2_id]
            if match:
                _apply_s2_enrichment(article, match)
                s2_matched += 1

    await _enrich_openalex(oa_client, articles)
    openalex_matched = sum(1 for a in articles if a.concepts)

    included_path.write_text(json.dumps([a.model_dump(mode="json") for a in articles], indent=2))

    review.status = ReviewStatus.enriched
    state_manager.save(review, review_dir)

    return {
        "enriched": len(articles),
        "s2_matched": s2_matched,
        "openalex_matched": openalex_matched,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich included articles with citation metadata")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    args = parser.parse_args()

    async def _main() -> None:
        try:
            result = await enrich(Path(args.review_dir))
            print(json.dumps(result, indent=2))
        except Exception as exc:
            print(json.dumps({"error": str(exc)}))
            sys.exit(1)

    asyncio.run(_main())
