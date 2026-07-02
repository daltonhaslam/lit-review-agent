"""Deduplicate articles using a three-tier priority scheme.

Priority order (first match wins):
  1. DOI exact match (case-insensitive, strip https://doi.org/ prefix)
  2. Title similarity ≥ 0.92 (rapidfuzz token_sort_ratio)
  3. First-author family name + year + title similarity ≥ 0.70 (fallback)

Duplicates are merged: source_dbs lists are combined so every source where
the article appeared is preserved. The canonical record is the first-seen one.

Input:  review_dir (reads articles_raw.json; prior status must be aggregated)
Output: articles_deduped.json; prisma_counts.identified updated; state=deduplicated
CLI:    python -m src.skills.deduplicator --review-dir reviews/{slug}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.article import Article
from src.models.review import ReviewStatus

TITLE_SIM_THRESHOLD = 0.92
FALLBACK_TITLE_THRESHOLD = 0.70


def deduplicate(review_dir: Path) -> dict:
    """Deduplicate articles_raw.json → articles_deduped.json; update state."""
    review = state_manager.load(review_dir)
    articles_raw_path = review_dir / review.articles_raw_file

    raw = json.loads(articles_raw_path.read_text())
    articles = [Article(**a) for a in raw]

    deduped, report = _run_dedup(articles)

    deduped_path = review_dir / review.articles_deduped_file
    deduped_path.write_text(
        json.dumps([a.model_dump(mode="json") for a in deduped], indent=2)
    )

    review.status = ReviewStatus.deduplicated
    review.prisma_counts.identified = len(deduped)
    state_manager.save(review, review_dir)

    return {
        "before": len(articles),
        "after": len(deduped),
        "removed": len(articles) - len(deduped),
        "by_method": report,
        "prisma_identified": len(deduped),
    }


def _run_dedup(articles: list[Article]) -> tuple[list[Article], dict]:
    """Core deduplication logic. Returns (deduped list, removal report)."""
    seen_dois: dict[str, int] = {}       # normalized_doi → index in result
    result: list[Article] = []
    result_titles: list[str] = []        # pre-normalized titles; parallel to result
    report = {"doi_match": 0, "title_similarity": 0, "author_year_fallback": 0}

    for article in articles:
        doi = _normalize_doi(article.doi)
        title_norm = _normalize_title(article.title)

        if doi and doi in seen_dois:
            _merge_sources(result[seen_dois[doi]], article)
            report["doi_match"] += 1
            continue

        matched_idx = _find_by_title(title_norm, result_titles, TITLE_SIM_THRESHOLD)
        if matched_idx is not None:
            _merge_sources(result[matched_idx], article)
            # A DOI-less canonical may have just acquired this article's DOI; register
            # it so a later article carrying the same DOI is caught by the DOI tier.
            _register_doi(result[matched_idx], matched_idx, seen_dois)
            report["title_similarity"] += 1
            continue

        if article.year and article.authors:
            matched_idx = _find_by_author_year(article, title_norm, result, result_titles)
            if matched_idx is not None:
                _merge_sources(result[matched_idx], article)
                # Same reason as the title path: a DOI acquired via merge must be
                # indexed, or a later same-DOI record falls through all tiers.
                _register_doi(result[matched_idx], matched_idx, seen_dois)
                report["author_year_fallback"] += 1
                continue

        if doi:
            seen_dois[doi] = len(result)
        result.append(article)
        result_titles.append(title_norm)

    return result, report


def _register_doi(canonical: Article, idx: int, seen_dois: dict[str, int]) -> None:
    """Index the canonical's normalized DOI (idempotent; keeps first-seen index)."""
    doi = _normalize_doi(canonical.doi)
    if doi:
        seen_dois.setdefault(doi, idx)


def _normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.lower().strip()
    doi = re.sub(r"^https?://doi\.org/", "", doi)
    return doi or None


def _normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _find_by_title(
    title_norm: str, candidate_titles: list[str], threshold: float
) -> int | None:
    for i, existing_norm in enumerate(candidate_titles):
        if fuzz.token_sort_ratio(title_norm, existing_norm) / 100.0 >= threshold:
            return i
    return None


def _find_by_author_year(
    article: Article,
    title_norm: str,
    candidates: list[Article],
    candidate_titles: list[str],
) -> int | None:
    first_family = _family_name(article.authors[0])
    if not first_family:
        return None

    for i, existing in enumerate(candidates):
        if existing.year != article.year:
            continue
        if not existing.authors:
            continue
        if _family_name(existing.authors[0]).lower() != first_family.lower():
            continue
        if fuzz.token_sort_ratio(title_norm, candidate_titles[i]) / 100.0 >= FALLBACK_TITLE_THRESHOLD:
            return i
    return None


def _family_name(author_str: str) -> str:
    return author_str.strip().split()[0] if author_str.strip() else ""


def _merge_sources(canonical: Article, duplicate: Article) -> None:
    canonical.source_dbs = sorted(set(canonical.source_dbs + duplicate.source_dbs))
    if not canonical.abstract and duplicate.abstract:
        canonical.abstract = duplicate.abstract
    if not canonical.doi and duplicate.doi:
        canonical.doi = duplicate.doi
    if not canonical.pmid and duplicate.pmid:
        canonical.pmid = duplicate.pmid
    if canonical.citation_count is None and duplicate.citation_count is not None:
        canonical.citation_count = duplicate.citation_count
    if not canonical.tldr and duplicate.tldr:
        canonical.tldr = duplicate.tldr


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deduplicate retrieved articles")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    args = parser.parse_args()

    try:
        result = deduplicate(Path(args.review_dir))
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
