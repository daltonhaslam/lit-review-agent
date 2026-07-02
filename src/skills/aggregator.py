"""Normalize and enrich raw articles with CrossRef DOI resolution.

Reads articles_raw.json (written by multi_source_retriever), resolves missing DOIs
via CrossRef for articles that have a PMID but no DOI, then writes back to the
same file. Updates state to status=aggregated.

DOI resolution is best-effort: failures are silently skipped. PubMed usually
provides DOIs in efetch; this pass catches any gaps from other sources.

Input:  review_dir (reads articles_raw.json from state)
Output: articles_raw.json (re-written with resolved DOIs); state updated
CLI:    python -m src.skills.aggregator --review-dir reviews/{slug}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.integrations.config import load_api_config
from src.integrations.crossref_client import CrossRefClient
from src.models.article import Article
from src.models.review import ReviewStatus


async def aggregate(review_dir: Path) -> dict:
    """Resolve missing DOIs and normalize; update state to aggregated."""
    review = state_manager.load(review_dir)
    articles_raw_path = review_dir / review.articles_raw_file

    raw = json.loads(articles_raw_path.read_text())
    articles = [Article(**a) for a in raw]

    cfg = load_api_config()
    crossref = CrossRefClient(mailto=cfg.crossref_mailto)

    articles = await _resolve_dois(articles, crossref)

    articles_raw_path.write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )

    review.status = ReviewStatus.aggregated
    state_manager.save(review, review_dir)

    missing_doi = sum(1 for a in articles if not a.doi)
    return {
        "total": len(articles),
        "with_doi": len(articles) - missing_doi,
        "without_doi": missing_doi,
    }


async def _resolve_dois(
    articles: list[Article], crossref: CrossRefClient
) -> list[Article]:
    """For articles missing a DOI, attempt CrossRef lookup via title + first author."""
    needs_resolution = [a for a in articles if not a.doi and a.title]
    if not needs_resolution:
        return articles

    sem = asyncio.Semaphore(3)

    async def try_resolve(article: Article) -> None:
        async with sem:
            doi = await _crossref_lookup(article, crossref)
            if doi:
                article.doi = doi

    await asyncio.gather(*[try_resolve(a) for a in needs_resolution], return_exceptions=True)
    return articles


async def _crossref_lookup(article: Article, crossref: CrossRefClient) -> str | None:
    first_author_family = _family_name(article.authors[0]) if article.authors else ""
    return await crossref.search_by_title(
        title=article.title,
        first_author_family=first_author_family,
        year=article.year,
    )


def _family_name(author_str: str) -> str:
    parts = author_str.strip().split()
    return parts[0] if parts else ""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aggregate and DOI-resolve raw articles")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    args = parser.parse_args()

    async def _main() -> None:
        try:
            result = await aggregate(Path(args.review_dir))
            print(json.dumps(result, indent=2))
        except Exception as exc:
            print(json.dumps({"error": str(exc)}))
            sys.exit(1)

    asyncio.run(_main())
