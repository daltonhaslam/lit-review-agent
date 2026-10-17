"""Multi-source parallel literature retrieval.

Runs searches across PubMed, OpenAlex, Semantic Scholar, and optionally Europe PMC
in parallel using asyncio.gather. Results are tagged by source DB and written to
articles_raw.json in the review directory.

Input:  review_dir (reads queries from state); optional source list and max_per_source
Output: articles_raw.json; state.json updated to status=retrieved
CLI:    python -m src.skills.multi_source_retriever --review-dir reviews/{slug}
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
from src.integrations.errors import SourceRetrievalError
from src.integrations.europepmc_client import EuropePMCClient
from src.integrations.openalex_client import OpenAlexClient
from src.integrations.pubmed_client import PubMedClient
from src.integrations.semantic_scholar_client import SemanticScholarClient
from src.integrations.sources import DEFAULT_SOURCES, SOURCE_DISPLAY_NAMES, display_names
from src.models.article import Article
from src.models.review import ReviewStatus

DEFAULT_MAX_PER_SOURCE = 500


async def retrieve(
    review_dir: Path,
    sources: list[str] | None = None,
    max_per_source: int = DEFAULT_MAX_PER_SOURCE,
    year_range: tuple[int, int] | None = None,
) -> dict:
    """Search all sources in parallel; write articles_raw.json; update state."""
    review = state_manager.load(review_dir)
    if not review.queries or not review.queries.pubmed:
        raise ValueError("Queries not built — run query_builder first.")

    sources = [s.lower() for s in (sources or DEFAULT_SOURCES)]
    cfg = load_api_config()

    pubmed_query = review.queries.pubmed[-1].query_string
    openalex_query = (
        review.queries.openalex[0].query_string if review.queries.openalex else pubmed_query
    )
    s2_query = (
        review.queries.semantic_scholar[0].query_string
        if review.queries.semantic_scholar
        else pubmed_query
    )
    europepmc_query = (
        review.queries.europepmc[0].query_string
        if review.queries.europepmc
        else pubmed_query
    )

    tasks: list[asyncio.Task] = []
    task_labels: list[str] = []

    if "pubmed" in sources:
        tasks.append(asyncio.create_task(
            _search_pubmed(pubmed_query, max_per_source, cfg, year_range)
        ))
        task_labels.append("pubmed")

    if "openalex" in sources:
        tasks.append(asyncio.create_task(
            _search_openalex(openalex_query, max_per_source, year_range, cfg)
        ))
        task_labels.append("openalex")

    if "semantic_scholar" in sources:
        tasks.append(asyncio.create_task(
            _search_semantic_scholar(s2_query, max_per_source, year_range, cfg)
        ))
        task_labels.append("semantic_scholar")

    if "europepmc" in sources:
        tasks.append(asyncio.create_task(
            _search_europepmc(europepmc_query, max_per_source, year_range)
        ))
        task_labels.append("europepmc")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles: list[Article] = []
    by_source: dict[str, int] = {}
    errors: dict[str, str] = {}
    # A degraded source failed (rate limit, server, network) rather than matching nothing;
    # its count is whatever it managed before failing. Message keyed by source label.
    degraded: dict[str, str] = {}

    for label, result in zip(task_labels, results):
        if isinstance(result, Exception):
            if not isinstance(result, SourceRetrievalError):
                result = SourceRetrievalError.from_exception(label, result)
            articles = result.partial
            degraded[label] = f"DEGRADED: {result}"
            errors[label] = degraded[label]
        else:
            articles = result
            if not articles:
                errors[label] = (
                    f"WARNING: {label} returned 0 results — query may be too specific "
                    f"or syntactically invalid. Inspect the query in state.queries.{label}."
                )
        by_source[label] = len(articles)
        all_articles.extend(articles)

    source_status = {
        label: "degraded" if label in degraded else ("ok" if by_source[label] else "empty")
        for label in task_labels
    }

    articles_raw_path = review_dir / review.articles_raw_file
    articles_raw_path.write_text(
        json.dumps([a.model_dump(mode="json") for a in all_articles], indent=2)
    )

    _write_hits_to_queries(review, by_source, degraded)
    _write_search_log(review, review_dir, by_source, sources, degraded)

    review.status = ReviewStatus.retrieved
    review.sources_searched = sources
    state_manager.save(review, review_dir)

    return {
        "total": len(all_articles),
        "by_source": by_source,
        "source_status": source_status,
        "degraded_sources": list(degraded),
        "retrieval_errors": errors,
        "articles_raw_file": str(articles_raw_path),
    }


def _write_hits_to_queries(
    review, by_source: dict[str, int], degraded: dict[str, str] | None = None
) -> None:
    for source in ("pubmed", "openalex", "semantic_scholar", "europepmc"):
        queries = getattr(review.queries, source)
        if source in by_source and queries:
            queries[-1].hits = by_source[source]
            if degraded and source in degraded:
                # Append: notes already holds the query-construction rationale from Stage 4.
                # Skip if a re-run already recorded this exact degradation.
                existing = queries[-1].notes or ""
                if degraded[source] not in existing:
                    queries[-1].notes = f"{existing} | {degraded[source]}" if existing else degraded[source]


def _write_search_log(
    review,
    review_dir: Path,
    by_source: dict[str, int],
    sources: list[str] | None = None,
    degraded: dict[str, str] | None = None,
) -> None:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Use the sources actually searched this run; review.sources_searched is only assigned
    # after this function is called, so relying on it would emit a blank/stale header.
    searched = sources if sources is not None else review.sources_searched
    db_names = display_names(searched)

    lines = [
        "# Search Log",
        "",
        f"**Date:** {date_str}",
        f"**Databases searched:** {', '.join(db_names)}",
        "",
    ]

    if "pubmed" in by_source and review.queries.pubmed:
        lines.append("## PubMed")
        for q in review.queries.pubmed:
            lines.append(f"- {q.step_number}: `{q.query_string}`")
        lines.append(f"- Articles retrieved: {by_source['pubmed']}")
        _append_degraded_line(lines, "pubmed", degraded)
        lines.append("")

    for db in ("openalex", "semantic_scholar", "europepmc"):
        if db not in by_source:
            continue
        queries = getattr(review.queries, db)
        db_name = SOURCE_DISPLAY_NAMES.get(db, db)
        lines.append(f"## {db_name}")
        if queries:
            lines.append(f"- Query: `{queries[0].query_string}`")
        lines.append(f"- Articles retrieved: {by_source[db]}")
        _append_degraded_line(lines, db, degraded)
        lines.append("")

    (review_dir / "search_log.md").write_text("\n".join(lines), encoding="utf-8")


def _append_degraded_line(lines: list[str], source: str, degraded: dict[str, str] | None) -> None:
    if degraded and source in degraded:
        lines.append(
            f"- **{degraded[source]}** (count above is partial or zero because the source "
            "failed, not because nothing matched)"
        )


async def _search_pubmed(
    query: str,
    max_results: int,
    cfg,
    year_range: tuple[int, int] | None = None,
) -> list[Article]:
    if year_range:
        year_from, year_to = year_range
        query = f"{query} AND ({year_from}:{year_to}[pdat])"
    client = PubMedClient(api_key=cfg.ncbi_api_key)
    result = await client.search(query, max_results=max_results)
    pmids = result["pmids"]
    if not pmids:
        return []
    return await client.fetch_abstracts(pmids[:max_results])


async def _search_openalex(
    query: str,
    max_results: int,
    year_range: tuple[int, int] | None,
    cfg,
) -> list[Article]:
    client = OpenAlexClient(email=cfg.openalex_email)
    return await client.search(query, year_range=year_range, max_results=max_results)


async def _search_semantic_scholar(
    query: str,
    max_results: int,
    year_range: tuple[int, int] | None,
    cfg,
) -> list[Article]:
    client = SemanticScholarClient(api_key=cfg.semantic_scholar_api_key)
    return await client.search(query, max_results=max_results, year_range=year_range)


async def _search_europepmc(
    query: str,
    max_results: int,
    year_range: tuple[int, int] | None = None,
) -> list[Article]:
    if year_range:
        year_from, year_to = year_range
        query = f"{query} AND FIRST_PDATE:[{year_from} TO {year_to}]"
    client = EuropePMCClient()
    return await client.search(query, max_results=max_results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-source literature retrieval")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_SOURCES),
        help="Comma-separated list of sources to search",
    )
    parser.add_argument("--max-per-source", type=int, default=DEFAULT_MAX_PER_SOURCE)
    parser.add_argument("--year-from", type=int, default=None)
    parser.add_argument("--year-to", type=int, default=None)
    args = parser.parse_args()

    year_range = None
    if args.year_from:
        year_to = args.year_to or datetime.now().year
        year_range = (args.year_from, year_to)

    async def _main() -> None:
        try:
            result = await retrieve(
                Path(args.review_dir),
                sources=args.sources.split(","),
                max_per_source=args.max_per_source,
                year_range=year_range,
            )
            print(json.dumps(result, indent=2))
        except Exception as exc:
            print(json.dumps({"error": str(exc)}))
            sys.exit(1)

    asyncio.run(_main())
