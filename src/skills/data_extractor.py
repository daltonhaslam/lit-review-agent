"""Structured data extraction from included articles.

--prepare: loads included articles, formats one batch for Claude to extract from.
--save:    applies Claude's per-article extractions; finalizes when coverage is complete.

Input:  review_dir (reads articles_included.json; status must be fulltext_assessed)
Output: articles_included.json updated per batch; evidence_table.csv when complete;
        status=data_extracted when complete
CLI:
  # Serial mode (single batch; advance --batch-offset across calls):
  python -m src.skills.data_extractor --review-dir reviews/{slug} --prepare \\
      --batch-size 50 --batch-offset 0
  # Parallel mode (one slice per parallel sub-agent):
  python -m src.skills.data_extractor --review-dir reviews/{slug} --prepare \\
      --slice-of N --slice-index I
  python -m src.skills.data_extractor --review-dir reviews/{slug} --save \\
      --offset 0 --input-json '{"extractions": [...]}'
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.article import Article, ExtractionSource
from src.models.review import ReviewStatus
from src.skills._base import die, parse_save_input, prepare_output, slice_bounds, validate_slice_spec
from src.skills._study_design import (
    CANONICAL_STUDY_DESIGNS,
    CANONICAL_TO_NHMRC,
    assign_study_design,
    canonicalize_study_design,
)
from src.skills.fulltext_assessor import FULLTEXT_MAX_CHARS

CSV_FIELDS = [
    "title", "authors", "year", "journal", "doi", "pmid",
    "data_source",
    "study_design", "sample_size", "follow_up_duration",
    "intervention_details", "comparison_details",
    "primary_outcomes", "effect_sizes", "secondary_outcomes",
    "author_conclusions", "funding_source", "coi_declared",
    "evidence_level", "quality_score", "quality_instrument",
]

EXTRACTION_TASK = (
    "Extract structured data from each article for the systematic review evidence table. "
    "If the article text contains a `[... middle of article truncated ...]` marker, the head and "
    "tail are from the same paper but non-contiguous — do not associate numbers across the seam. "
    "For most fields, extract only what is explicitly stated — use null if not clearly present. "
    "Exception: for author_conclusions, extract the main finding or takeaway from the abstract "
    "even if not labeled 'CONCLUSIONS' — use the final sentence(s) or results summary. "
    "Use null for author_conclusions only if no conclusion can be inferred at all.\n\n"
    "For each article provide:\n"
    "- study_design: pick the SINGLE closest term from this controlled list — return the term verbatim:\n"
    "    systematic_review, meta_analysis, umbrella_review,\n"
    "    rct, cluster_rct,\n"
    "    quasi_rct, non_randomized_trial, controlled_before_after, interrupted_time_series,\n"
    "    prospective_cohort, comparative_study,\n"
    "    retrospective_cohort, case_control, historical_control,\n"
    "    case_series, case_report, cross_sectional, qualitative, mixed_methods,\n"
    "    narrative_review, scoping_review, editorial, commentary, letter,\n"
    "    program_description, qi_report, survey, economic_evaluation, expert_opinion.\n"
    "  Use null only if NO term reasonably fits.\n"
    "- sample_size: integer or null\n"
    "- follow_up_duration: string or null (e.g. '6 months', '1 year')\n"
    "- intervention_details: string or null\n"
    "- comparison_details: string or null\n"
    "- primary_outcomes: list of strings (empty list if none stated)\n"
    "- effect_sizes: effect size, CI, and p-value in one string or null\n"
    "- secondary_outcomes: list of strings (empty list if none stated)\n"
    "- author_conclusions: string or null (see exception above)\n"
    "- funding_source: string or null\n"
    "- coi_declared: true/false/null\n\n"
    "Respond with JSON only:\n"
    '{"extractions": [{"orig_idx": N, "study_design": "...", "sample_size": N|null, '
    '"follow_up_duration": "...|null", "intervention_details": "...|null", '
    '"comparison_details": "...|null", "primary_outcomes": [...], "effect_sizes": "...|null", '
    '"secondary_outcomes": [...], "author_conclusions": "...|null", '
    '"funding_source": "...|null", "coi_declared": true|false|null}]}'
)


def _determine_extraction_source(article: Article) -> ExtractionSource:
    """Pick the highest-fidelity content available for this article."""
    if article.fulltext_excerpt:
        return ExtractionSource.full_text
    if article.abstract:
        return ExtractionSource.abstract
    if article.tldr:
        return ExtractionSource.tldr
    return ExtractionSource.none


def _format_article_text(
    article: Article,
    *,
    max_abstract: int | None = None,
    include_sample_size: bool = False,
) -> str:
    parts = [f"Title: {article.title}"]
    if article.authors:
        parts.append(f"Authors: {', '.join(article.authors[:3])}" + (" et al." if len(article.authors) > 3 else ""))
    if article.year:
        parts.append(f"Year: {article.year}")
    if article.journal:
        parts.append(f"Journal: {article.journal}")
    if article.study_design:
        parts.append(f"Known study design: {article.study_design}")
    if include_sample_size and article.sample_size:
        parts.append(f"Sample size: {article.sample_size}")

    source = _determine_extraction_source(article)
    if source is ExtractionSource.full_text:
        # Forward the excerpt verbatim — do NOT re-slice on FULLTEXT_MAX_CHARS. The excerpt is a
        # head+tail composition (first HEAD + '[... truncated ...]' + last TAIL chars); slicing it
        # to [:MAX] would cut the tail and destroy the seam. It is already bounded at extraction.
        parts.append(f"\nFull text (extracted from PDF):\n{article.fulltext_excerpt}")
    elif source is ExtractionSource.abstract:
        text = article.abstract[:max_abstract] if max_abstract else article.abstract
        parts.append(f"\nAbstract:\n{text}")
    elif source is ExtractionSource.tldr:
        parts.append(f"\nTLDR: {article.tldr}")
    return "\n".join(parts)


def _apply_extraction(article: Article, data: dict) -> None:
    if data.get("study_design") and not article.study_design:
        assign_study_design(article, data["study_design"])
    if data.get("sample_size") is not None and article.sample_size is None:
        try:
            article.sample_size = int(data["sample_size"])
        except (TypeError, ValueError):
            pass
    if data.get("follow_up_duration"):
        article.follow_up_duration = data["follow_up_duration"]
    if data.get("intervention_details"):
        article.intervention_details = data["intervention_details"]
    if data.get("comparison_details"):
        article.comparison_details = data["comparison_details"]
    if data.get("primary_outcomes") is not None:
        article.primary_outcomes = data["primary_outcomes"]
    if data.get("effect_sizes") is not None:
        article.effect_sizes = data["effect_sizes"]
    if data.get("secondary_outcomes") is not None:
        article.secondary_outcomes = data["secondary_outcomes"]
    if data.get("author_conclusions"):
        article.author_conclusions = data["author_conclusions"]
    if data.get("funding_source"):
        article.funding_source = data["funding_source"]
    if data.get("coi_declared") is not None:
        article.coi_declared = bool(data["coi_declared"])


def _write_evidence_table(articles: list[Article], output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for a in articles:
            # Backfill data_source for older reviews whose extraction predates this field.
            data_source = a.extraction_source or _determine_extraction_source(a)
            writer.writerow({
                "title": a.title,
                "authors": "; ".join(a.authors[:3]) + (" et al." if len(a.authors) > 3 else ""),
                "year": a.year or "",
                "journal": a.journal or "",
                "doi": a.doi or "",
                "pmid": a.pmid or "",
                "data_source": data_source.value,
                "study_design": a.study_design or "",
                "sample_size": a.sample_size if a.sample_size is not None else "",
                "follow_up_duration": a.follow_up_duration or "",
                "intervention_details": a.intervention_details or "",
                "comparison_details": a.comparison_details or "",
                "primary_outcomes": "; ".join(a.primary_outcomes),
                "effect_sizes": a.effect_sizes or "",
                "secondary_outcomes": "; ".join(a.secondary_outcomes),
                "author_conclusions": a.author_conclusions or a.tldr or "",
                "funding_source": a.funding_source or "",
                "coi_declared": ("Yes" if a.coi_declared is True else "No" if a.coi_declared is False else ""),
                "evidence_level": a.evidence_level.value if a.evidence_level else "",
                "quality_score": f"{a.quality_score:.2f}" if a.quality_score is not None else "",
                "quality_instrument": a.quality_instrument or "",
            })


def refresh_table(review_dir: Path) -> None:
    review = state_manager.load(review_dir)
    included_path = review_dir / review.articles_included_file
    articles = [Article(**a) for a in json.loads(included_path.read_text())]
    _write_evidence_table(articles, review_dir / "evidence_table.csv")
    print(prepare_output({"refreshed": len(articles)}))


def canonicalize_existing(review_dir: Path) -> None:
    """Backfill canonical study_design IDs onto an already-extracted review.

    Walks articles_included.json, applies _canonicalize_study_design to each
    article's study_design, writes back. No LLM call. Idempotent: already-canonical
    values stay unchanged; values with no mapping are left intact for
    evidence_classifier's substring fallback.
    """
    review = state_manager.load(review_dir)
    included_path = review_dir / review.articles_included_file
    articles = [Article(**a) for a in json.loads(included_path.read_text())]

    changed = 0
    unmapped: list[tuple[int, str]] = []
    for i, article in enumerate(articles):
        raw = article.study_design
        if not raw:
            continue
        canonical = canonicalize_study_design(raw)
        if canonical is None:
            unmapped.append((i, raw))
            continue
        if canonical != raw:
            article.study_design = canonical
            changed += 1

    summary = {
        "total": len(articles),
        "canonicalized": changed,
        "unmapped": len(unmapped),
        "unmapped_examples": [{"orig_idx": i, "raw": s} for i, s in unmapped[:10]],
    }
    if changed > 0:
        included_path.write_text(json.dumps([a.model_dump(mode="json") for a in articles], indent=2))
        _write_evidence_table(articles, review_dir / "evidence_table.csv")
    print(prepare_output(summary))


def _load_included(review_dir: Path, review) -> tuple[Path, list[Article]]:
    included_path = review_dir / review.articles_included_file
    return included_path, [Article(**a) for a in json.loads(included_path.read_text())]


BATCH_SIZE_DEFAULT = 50

# Per-field coverage thresholds for Stage 11 warnings.
# CONCLUSIONS — EXTRACTION_TASK explicitly permits inference, so coverage should be high
# regardless of input fidelity. <80% indicates extraction quality is degraded.
_CONCLUSIONS_THRESHOLD = 0.80
# FUNDING — abstracts rarely include funding; only meaningful when full text was available.
# Threshold gates on `had_full_text` so abstract-only reviews don't fire spurious warnings.
_FUNDING_THRESHOLD = 0.30


def _build_coverage_and_warnings(articles: list[Article]) -> tuple[dict, list[str]]:
    """Return (extraction_coverage_dict, warnings_list).

    Coverage tallies presence per field; warnings flag patterns that meaningfully
    degrade synthesis: low conclusions coverage, low funding when full text was
    available, and any articles whose extraction input was empty.
    """
    total = len(articles)
    counts = {
        "study_design": 0,
        "effect_sizes": 0,
        "primary_outcomes": 0,
        "funding": 0,
        "coi": 0,
        "conclusions": 0,
        "intervention": 0,
        "comparison": 0,
    }
    source_dist: dict[str, int] = {}
    empty_input_idxs: list[int] = []

    for i, a in enumerate(articles):
        if a.study_design: counts["study_design"] += 1
        if a.effect_sizes: counts["effect_sizes"] += 1
        if a.primary_outcomes: counts["primary_outcomes"] += 1
        if a.funding_source: counts["funding"] += 1
        if a.coi_declared is not None: counts["coi"] += 1
        if a.author_conclusions: counts["conclusions"] += 1
        if a.intervention_details: counts["intervention"] += 1
        if a.comparison_details: counts["comparison"] += 1
        src = (a.extraction_source or _determine_extraction_source(a))
        source_dist[src.value] = source_dist.get(src.value, 0) + 1
        if src is ExtractionSource.none:
            empty_input_idxs.append(i)

    coverage = {
        "total": total,
        "with_study_design": counts["study_design"],
        "with_effect_sizes": counts["effect_sizes"],
        "with_primary_outcomes": counts["primary_outcomes"],
        "with_funding": counts["funding"],
        "with_coi": counts["coi"],
        "with_conclusions": counts["conclusions"],
        "with_intervention": counts["intervention"],
        "with_comparison": counts["comparison"],
        "articles_with_empty_input": len(empty_input_idxs),
        "effect_size_pct": round(counts["effect_sizes"] / total, 3) if total else 0.0,
        "data_source_distribution": source_dist,
    }

    warnings: list[str] = []
    if total == 0:
        return coverage, warnings

    conclusions_pct = counts["conclusions"] / total
    if conclusions_pct < _CONCLUSIONS_THRESHOLD:
        warnings.append(
            f"author_conclusions populated for {counts['conclusions']}/{total} "
            f"({conclusions_pct * 100:.0f}%); below {_CONCLUSIONS_THRESHOLD * 100:.0f}% — "
            "extraction quality is degraded. EXTRACTION_TASK permits inference from the "
            "abstract tail; null usually means the input had no usable text."
        )

    had_full_text = source_dist.get("full_text", 0) > 0
    funding_pct = counts["funding"] / total
    if had_full_text and funding_pct < _FUNDING_THRESHOLD:
        warnings.append(
            f"funding_source extracted for only {counts['funding']}/{total} "
            f"({funding_pct * 100:.0f}%); expected ≥{_FUNDING_THRESHOLD * 100:.0f}% when "
            "full text is available. Check that PDFs include acknowledgments/funding sections."
        )

    if empty_input_idxs:
        sample = empty_input_idxs[:10]
        more = "" if len(empty_input_idxs) <= 10 else f" (+{len(empty_input_idxs) - 10} more)"
        warnings.append(
            f"{len(empty_input_idxs)} article(s) had empty input text (no PDF and no abstract); "
            f"these were not meaningfully extracted: orig_idx {sample}{more}. "
            "Resolve by obtaining the PDFs and running --persist-pdf-text + re-running Stage 11."
        )

    return coverage, warnings


def prepare(
    review_dir: Path,
    batch_size: int = BATCH_SIZE_DEFAULT,
    batch_offset: int = 0,
    slice_of: int | None = None,
    slice_index: int | None = None,
) -> None:
    review = state_manager.load(review_dir)
    if review.status != ReviewStatus.fulltext_assessed:
        die(f"Expected status 'fulltext_assessed', got '{review.status}'.")
    validate_slice_spec(slice_of, slice_index)

    _, articles = _load_included(review_dir, review)
    total = len(articles)

    if slice_of is not None:
        start, end = slice_bounds(total, slice_of, slice_index)
        total_batches = slice_of
        batch_number = slice_index + 1
    else:
        if total > 0 and batch_offset >= total:
            die(f"batch_offset {batch_offset} >= total articles {total}.")
        start = batch_offset
        end = batch_offset + batch_size
        total_batches = math.ceil(total / batch_size) if total > 0 else 0
        batch_number = batch_offset // batch_size + 1

    batch_articles = articles[start:end]
    articles_out = [
        {"orig_idx": start + i, "article_text": _format_article_text(article)}
        for i, article in enumerate(batch_articles)
    ]

    output: dict = {
        "review_id": review.review_id,
        "batch_number": batch_number,
        "total_batches": total_batches,
        "batch_offset": start,
        "total_articles": total,
        "articles": articles_out,
        "extraction_task": EXTRACTION_TASK,
    }
    if slice_of is not None:
        output["slice_of"] = slice_of
        output["slice_index"] = slice_index
        output["slice_pending"] = len(batch_articles)
        output["slice_indices"] = list(range(start, end))
    print(prepare_output(output))


def save(
    review_dir: Path,
    input_json: str,
    batch_size: int = BATCH_SIZE_DEFAULT,
    batch_offset: int = 0,
) -> None:
    data = parse_save_input(input_json)
    extractions = data.get("extractions", [])

    review = state_manager.load(review_dir)
    included_path, articles = _load_included(review_dir, review)
    total = len(articles)

    extraction_map = {
        e["orig_idx"]: e for e in extractions if isinstance(e.get("orig_idx"), int)
    }
    for i, article in enumerate(articles):
        if i in extraction_map:
            _apply_extraction(article, extraction_map[i])
            # Reassign every save so backfills update articles whose source was previously None.
            article.extraction_source = _determine_extraction_source(article)

    included_path.write_text(json.dumps([a.model_dump(mode="json") for a in articles], indent=2))

    full_coverage = total > 0 and all(i in extraction_map for i in range(total))
    complete = full_coverage or batch_offset + batch_size >= total
    if complete:
        _write_evidence_table(articles, review_dir / "evidence_table.csv")
        review.status = ReviewStatus.data_extracted
        state_manager.save(review, review_dir)

    summary: dict = {
        "batch_saved": len(extractions),
        "total_articles": total,
        "complete": complete,
    }
    if complete:
        coverage, warnings = _build_coverage_and_warnings(articles)
        summary["evidence_table"] = "evidence_table.csv"
        summary["extraction_coverage"] = coverage
        summary["warnings"] = warnings
    elif batch_offset == 0:
        # Merged offset-0 path stalled short of full coverage: surface WHICH
        # orig_idx are missing so the orchestrator can re-run just that slice.
        # Scoped to offset 0 so serial mid-run batches don't report a noisy list
        # of every not-yet-processed index.
        summary["missing_orig_idx"] = [i for i in range(total) if i not in extraction_map]
    print(prepare_output(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Data extractor — subscription mode")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--prepare", action="store_true")
    mode_group.add_argument("--save", action="store_true")
    mode_group.add_argument("--refresh-table", action="store_true")
    mode_group.add_argument("--canonicalize-existing", action="store_true",
                            help="Backfill canonical study_design IDs onto already-extracted reviews; no LLM call.")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT, metavar="N")
    parser.add_argument("--batch-offset", type=int, default=0, metavar="N")
    parser.add_argument("--slice-of", type=int, default=None, metavar="N")
    parser.add_argument("--slice-index", type=int, default=None, metavar="I")
    parser.add_argument("--offset", type=int, default=0, metavar="N",
                        help="Batch start offset for --save (matches --batch-offset from --prepare)")
    parser.add_argument("--input-json", default=None, metavar="JSON")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)

    if args.prepare:
        prepare(review_dir, args.batch_size, args.batch_offset, args.slice_of, args.slice_index)
    elif args.refresh_table:
        refresh_table(review_dir)
    elif args.canonicalize_existing:
        canonicalize_existing(review_dir)
    else:
        if args.input_json is None:
            die("--input-json is required with --save.")
        save(review_dir, args.input_json, args.batch_size, args.offset)
