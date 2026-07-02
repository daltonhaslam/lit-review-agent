"""Iteration loop: retrieve from gap-suggested queries, dedup against seen, screen new, merge includes.

Reads gap_analysis.json (written by gap_analyzer) to get suggested queries. Filters against
review.seen_article_ids plus all articles in articles_screened.json to avoid re-screening.
New articles are output for Claude to screen (abstract_screener-style batching).
New includes are merged into articles_included.json; PRISMA counts and iteration counter updated.

Input:  review_dir (status must be gap_analyzed; iterations < max_iterations)
Output (--retrieve): candidates for Claude to screen + writes _iteration_candidates.json
Output (--merge):    articles_screened.json (appended, include+borderline flagged iteration_added),
                     state=citation_chased. New candidates then re-enter Stage 9 → 9.5 → 10
                     (fulltext_assessor --iteration-only appends only survivors) — same two gates
                     as the primary set. --merge does NOT write articles_included.json.
CLI:
  python -m src.skills.iteration_manager --review-dir reviews/{slug} --retrieve
  python -m src.skills.iteration_manager --review-dir reviews/{slug} --merge --decisions-json '{"decisions": [...]}'
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.integrations.config import load_api_config
from src.integrations.pubmed_client import PubMedClient
from src.models.article import Article, ScreeningDecision
from src.models.review import Review, ReviewStatus
from src.skills._base import die, parse_save_input, prepare_output
from src.skills.deduplicator import _normalize_doi

_CANDIDATES_FILE = "_iteration_candidates.json"

_SCREENING_TASK = (
    "These articles were retrieved from gap-filling queries for an existing systematic review. "
    "Screen each against the PICO criteria and inclusion/exclusion criteria provided. "
    "For each article decide: include, exclude, or borderline.\n\n"
    "For exclude decisions, set reason_category to one of: wrong_population, wrong_intervention, "
    "wrong_comparator, wrong_outcome, wrong_study_design, not_original_research, other.\n"
    "For include/borderline decisions, set reason_category to null.\n\n"
    "Respond with JSON only:\n"
    '{"decisions": [{"article_index": N, "decision": "include|exclude|borderline", '
    '"reason": "1-2 sentences", "reason_category": "..."|null}]}'
)


def _fingerprint(article: Article) -> str:
    doi = _normalize_doi(article.doi)
    if doi:
        return f"doi:{doi}"
    if article.pmid:
        return f"pmid:{article.pmid}"
    t = re.sub(r"[^\w]", "", (article.title or "").lower())[:80]
    return f"title:{t}"


def _build_seen_ids(
    review: Review,
    review_dir: Path,
    *,
    screened_raw: list[dict] | None = None,
    included_raw: list[dict] | None = None,
) -> set[str]:
    seen: set[str] = set(review.seen_article_ids)

    if screened_raw is None:
        p = review_dir / review.articles_screened_file
        screened_raw = json.loads(p.read_text()) if p.exists() else []
    for a_dict in screened_raw:
        seen.add(_fingerprint(Article(**a_dict)))

    if included_raw is None:
        p = review_dir / review.articles_included_file
        included_raw = json.loads(p.read_text()) if p.exists() else []
    for a_dict in included_raw:
        seen.add(_fingerprint(Article(**a_dict)))

    return seen


async def _fetch_query(
    client: PubMedClient, query: str, max_per_query: int
) -> tuple[str, list[Article]]:
    result = await client.search(query, max_results=max_per_query)
    pmids = result.get("pmids", [])
    articles = await client.fetch_abstracts(pmids) if pmids else []
    return query, articles


async def _retrieve_for_queries(
    cfg: dict,
    queries: list[str],
    seen_ids: set[str],
    max_per_query: int = 200,
) -> tuple[list[Article], dict[str, int], set[str]]:
    """Search PubMed for each query in parallel; return only articles not in seen_ids."""
    client = PubMedClient(cfg.get("ncbi_api_key", ""))

    results = await asyncio.gather(*[
        _fetch_query(client, q, max_per_query) for q in queries
    ])

    new_articles: list[Article] = []
    working_seen = set(seen_ids)
    query_hits: dict[str, int] = {}

    for query, articles in results:
        query_hits[query[:60]] = len(articles)
        for a in articles:
            fp = _fingerprint(a)
            if fp not in working_seen:
                new_articles.append(a)
                working_seen.add(fp)

    return new_articles, query_hits, working_seen


# ---------------------------------------------------------------------------
# --retrieve
# ---------------------------------------------------------------------------

async def _retrieve_async(review_dir: Path) -> None:
    review = state_manager.load(review_dir)

    if review.status != ReviewStatus.gap_analyzed:
        die(
            f"Expected status 'gap_analyzed', got '{review.status}'. "
            "Run gap_analyzer before iterating."
        )
    if review.iterations >= review.max_iterations:
        die(
            f"Max iterations ({review.max_iterations}) already reached. "
            "Proceed to synthesis or increase max_iterations in settings.yaml."
        )

    gap_path = review_dir / "gap_analysis.json"
    if not gap_path.exists():
        die("gap_analysis.json not found. Re-run gap_analyzer to generate it.")

    gap_data = json.loads(gap_path.read_text())
    suggested_queries = [q["query"] for q in gap_data.get("suggested_queries", []) if q.get("query")]
    if not suggested_queries:
        die("No suggested queries in gap_analysis.json. Cannot iterate.")

    screened_path = review_dir / review.articles_screened_file
    existing_screened = json.loads(screened_path.read_text()) if screened_path.exists() else []
    included_path = review_dir / review.articles_included_file
    existing_included = json.loads(included_path.read_text()) if included_path.exists() else []

    seen_ids = _build_seen_ids(
        review, review_dir,
        screened_raw=existing_screened,
        included_raw=existing_included,
    )
    cfg = load_api_config()
    new_articles, query_hits, updated_seen = await _retrieve_for_queries(cfg, suggested_queries, seen_ids)

    if not new_articles:
        review.seen_article_ids = list(updated_seen)
        review.iterations += 1
        state_manager.save(review, review_dir)
        print(prepare_output({
            "no_new_articles": True,
            "iteration": review.iterations,
            "queries_run": len(suggested_queries),
            "new_articles_found": 0,
            "query_hits": query_hits,
            "note": "No new articles found beyond previously-screened set.",
        }))
        return

    (review_dir / _CANDIDATES_FILE).write_text(
        json.dumps({
            "updated_seen": list(updated_seen),
            "candidates": [a.model_dump(mode="json") for a in new_articles],
        }, indent=2),
        encoding="utf-8",
    )

    pico = review.pico
    print(prepare_output({
        "no_new_articles": False,
        "total_new_candidates": len(new_articles),
        "queries_run": len(suggested_queries),
        "query_hits": query_hits,
        "articles": [
            {
                "article_index": i,
                "title": a.title,
                "abstract": (a.abstract or "")[:1500],
                "year": a.year,
            }
            for i, a in enumerate(new_articles)
        ],
        "pico": {"P": pico.P, "I": pico.I, "C": pico.C, "O": pico.O} if pico else {},
        "inclusion_criteria": pico.inclusion_criteria if pico else [],
        "exclusion_criteria": pico.exclusion_criteria if pico else [],
        "screening_task": _SCREENING_TASK,
    }))


def retrieve(review_dir: Path) -> None:
    asyncio.run(_retrieve_async(review_dir))


# ---------------------------------------------------------------------------
# --merge
# ---------------------------------------------------------------------------

def merge(review_dir: Path, decisions_json: str) -> None:
    candidates_path = review_dir / _CANDIDATES_FILE
    if not candidates_path.exists():
        die(f"{_CANDIDATES_FILE} not found. Run --retrieve first.")

    try:
        data = parse_save_input(decisions_json)
    except ValueError as exc:
        die(str(exc))

    candidates_data = json.loads(candidates_path.read_text())
    updated_seen = set(candidates_data.get("updated_seen", []))
    new_articles = [Article(**a) for a in candidates_data.get("candidates", [])]

    # Defensive parse (mirrors abstract_screener.save): a decision entry missing
    # "article_index" would otherwise raise KeyError and strand the iteration.
    decisions: dict[int, dict] = {}
    for d in data.get("decisions", []):
        idx = d.get("article_index")
        if isinstance(idx, int):
            decisions[idx] = d

    screened_articles: list[Article] = []
    new_includes: list[Article] = []
    new_borderlines: list[Article] = []
    for i, article in enumerate(new_articles):
        decision_data = decisions.get(i, {})
        decision_str = (decision_data.get("decision") or "borderline").lower()
        try:
            sd = ScreeningDecision(decision_str)
        except ValueError:
            sd = ScreeningDecision.borderline
        article.screening_decision = sd
        screened_articles.append(article)
        # Flag include AND borderline as iteration-added so the scoped Stage-10 full-text pass
        # (fulltext_assessor --iteration-only) picks them up. They are NOT added to
        # articles_included.json here — they must clear full-text assessment first, exactly like
        # the primary set (rigor parity). Borderlines especially need the full-text look.
        if sd in (ScreeningDecision.include, ScreeningDecision.borderline):
            article.iteration_added = True
        if sd == ScreeningDecision.include:
            new_includes.append(article)
        elif sd == ScreeningDecision.borderline:
            new_borderlines.append(article)

    review = state_manager.load(review_dir)

    screened_path = review_dir / review.articles_screened_file
    existing_screened = json.loads(screened_path.read_text()) if screened_path.exists() else []
    existing_screened.extend(a.model_dump(mode="json") for a in screened_articles)
    screened_path.write_text(json.dumps(existing_screened, indent=2), encoding="utf-8")

    review.prisma_counts.screened += len(screened_articles)
    review.seen_article_ids = list(updated_seen)
    review.iterations += 1
    # citation_chased: re-enter the normal Stage 9 (retrieval list) → 9.5 (PDF) → 10 (full-text)
    # flow so the new iteration candidates are held to the SAME two gates (abstract + full text)
    # as the primary set. The scoped `--iteration-only` Stage-10 pass appends only survivors.
    review.status = ReviewStatus.citation_chased
    state_manager.save(review, review_dir)

    candidates_path.unlink()

    print(prepare_output({
        "iteration": review.iterations,
        "queries_run": 0,
        "new_articles_found": len(screened_articles),
        "new_includes": len(new_includes),
        # Include + borderline both advance to the scoped full-text pass; these are NOT final
        # includes yet (they must clear Stage 10 first).
        "advancing_to_fulltext": len(new_includes) + len(new_borderlines),
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run one iteration: retrieve gap-suggested queries, screen new articles, merge includes"
    )
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--retrieve", action="store_true")
    mode.add_argument("--merge", action="store_true")
    parser.add_argument("--decisions-json", metavar="JSON")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)
    if args.retrieve:
        retrieve(review_dir)
    else:
        if not args.decisions_json:
            die("--merge requires --decisions-json")
        merge(review_dir, args.decisions_json)
