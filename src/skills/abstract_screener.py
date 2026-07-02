"""Abstract screening — subscription-mode (no Anthropic API).

--prepare: loads articles + PICO, formats one batch for Claude to screen in conversation.
--save:    applies Claude's decisions, accumulates progress, finalizes on last batch.

Input:  review_dir (reads articles_deduped.json; prior status must be deduplicated)
Output: articles_screened.json (written/updated on each --save);
        prisma_counts.screened + status=abstract_screening set on the final batch.
CLI:
  # Serial mode (single batch; auto-resume across calls):
  python -m src.skills.abstract_screener --review-dir reviews/{slug} --prepare \\
      --batch-size 50 --batch-offset 0
  # Parallel mode (one slice per parallel sub-agent):
  python -m src.skills.abstract_screener --review-dir reviews/{slug} --prepare \\
      --slice-of N --slice-index I
  python -m src.skills.abstract_screener --review-dir reviews/{slug} --save \\
      --offset 0 --decisions-json '{"decisions": [...]}'
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.review import ReviewStatus
from src.skills._base import die, parse_save_input, prepare_output, slice_bounds, validate_slice_spec

BATCH_SIZE_DEFAULT = 50
ABSTRACT_MAX_CHARS = 1500

SCREENING_TASK = (
    "Screen each article against the PICO(S) criteria and inclusion/exclusion criteria above. "
    "For each article decide: include, exclude, or borderline (use borderline only if the abstract "
    "is genuinely ambiguous and full-text is needed for a definitive call).\n\n"
    "For exclude decisions, set reason_category to one of: wrong_population, wrong_intervention, "
    "wrong_comparator, wrong_outcome, wrong_study_design, not_original_research, other.\n"
    "For include/borderline decisions, set reason_category to null.\n\n"
    "Respond with JSON only:\n"
    '{"decisions": [{"article_index": N, "decision": "include|exclude|borderline", '
    '"reason": "1-2 sentences", "reason_category": "..."|null}]}'
)


def _build_pico_context(review) -> str:
    pico = review.pico
    lines = [
        f"P (Population): {pico.P}",
        f"I (Intervention): {pico.I}",
        f"C (Comparator): {pico.C}",
        f"O (Outcomes): {', '.join(pico.O)}",
    ]
    if pico.S:
        lines.append(f"S (Study design): {pico.S}")
    lines.append("\nInclusion criteria:")
    lines.extend(f"  {i}. {c}" for i, c in enumerate(pico.inclusion_criteria, 1))
    lines.append("\nExclusion criteria:")
    lines.extend(f"  {i}. {c}" for i, c in enumerate(pico.exclusion_criteria, 1))
    return "\n".join(lines)


def _decision_from_str(s: str) -> ScreeningDecision:
    try:
        return ScreeningDecision(s.lower())
    except ValueError:
        return ScreeningDecision.borderline


def prepare(
    review_dir: Path,
    batch_size: int,
    batch_offset: int,
    slice_of: int | None = None,
    slice_index: int | None = None,
) -> None:
    review = state_manager.load(review_dir)
    if review.status != ReviewStatus.deduplicated:
        die(f"Expected status 'deduplicated', got '{review.status}'.")
    if review.pico is None:
        die("PICO not set. Run pico_refiner first.")
    validate_slice_spec(slice_of, slice_index)
    if slice_of is not None:
        batch_offset = 0  # slice mode partitions all currently pending articles

    screened_path = review_dir / review.articles_screened_file

    # Seed from screened file when it exists (preserves prior decisions + supports auto-resume)
    if screened_path.exists():
        articles_raw = json.loads(screened_path.read_text())
    else:
        articles_raw = json.loads((review_dir / review.articles_deduped_file).read_text())

    articles = [Article(**a) for a in articles_raw]
    total = len(articles)

    # Date prefilter: auto-exclude pending articles outside protocol date range (#25).
    # Single pass also accumulates decided_count and pending list for auto-resume (#24).
    date_range = review.date_range
    has_date_filter = bool(date_range and date_range.start and date_range.end)
    pre_filtered_count = 0
    pre_filtered_total = 0
    decided_count = 0
    pending: list = []

    for i, a in enumerate(articles):
        if (has_date_filter and a.screening_decision == ScreeningDecision.pending
                and a.year is not None
                and (a.year < date_range.start or a.year > date_range.end)):
            a.screening_decision = ScreeningDecision.exclude
            a.screening_reason_category = "date_out_of_range"
            a.screening_rationale = (
                f"Published outside protocol date range ({date_range.start}–{date_range.end})"
            )
            pre_filtered_count += 1
        if a.screening_reason_category == "date_out_of_range":
            pre_filtered_total += 1
        if a.screening_decision != ScreeningDecision.pending:
            decided_count += 1
        elif i >= batch_offset:
            pending.append((i, a))

    # Persist the date exclusions only from the non-sliced probe. In slice mode, N parallel
    # prepares would each do a full read-modify-write of the shared articles_screened.json and
    # race/tear it — the non-sliced Step-1 probe (which always runs first) persists them instead.
    if slice_of is None and pre_filtered_count > 0:
        screened_path.write_text(
            json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
        )

    if not pending:
        die(
            f"No pending articles remaining (decided: {decided_count}/{total}, "
            f"batch_offset: {batch_offset})."
        )

    if slice_of is not None:
        start, end = slice_bounds(len(pending), slice_of, slice_index)
        batch_items = pending[start:end]
        effective_offset = batch_items[0][0] if batch_items else pending[0][0]
        total_batches = slice_of
        batch_number = slice_index + 1
    else:
        batch_items = pending[:batch_size]
        effective_offset = batch_items[0][0]
        total_batches = math.ceil(total / batch_size)
        batch_number = effective_offset // batch_size + 1

    articles_out = []
    for i, a in batch_items:
        abstract = (a.abstract or "No abstract available")[:ABSTRACT_MAX_CHARS]
        articles_out.append({
            "article_index": i,
            "title": a.title,
            "abstract": abstract,
            "year": a.year,
            "pmid": a.pmid,
            "doi": a.doi,
        })

    pico = review.pico
    output = {
        "batch_number": batch_number,
        "total_batches": total_batches,
        "batch_offset": batch_offset,
        "total_articles": total,
        "pending_articles": len(pending),
        "auto_resume_offset": decided_count,
        "effective_offset": effective_offset,
        "pre_filtered_this_call": pre_filtered_count,
        "pre_filtered_total": pre_filtered_total,
        "articles": articles_out,
        "pico": {"P": pico.P, "I": pico.I, "C": pico.C, "O": pico.O, "S": pico.S},
        "inclusion_criteria": pico.inclusion_criteria,
        "exclusion_criteria": pico.exclusion_criteria,
        "screening_task": SCREENING_TASK,
    }
    if slice_of is not None:
        output["slice_of"] = slice_of
        output["slice_index"] = slice_index
        output["slice_pending"] = len(batch_items)
        output["slice_indices"] = [i for i, _ in batch_items]
    print(prepare_output(output))


def save(review_dir: Path, offset: int, decisions_json: str) -> None:
    data = parse_save_input(decisions_json)
    decisions = data.get("decisions", [])

    review = state_manager.load(review_dir)
    screened_path = review_dir / review.articles_screened_file

    # Seed from the screened file when it exists (preserves prior-batch decisions);
    # fall back to deduped on the first batch when nothing has been saved yet.
    if screened_path.exists():
        articles_raw = json.loads(screened_path.read_text())
    else:
        articles_raw = json.loads((review_dir / review.articles_deduped_file).read_text())

    articles = [Article(**a) for a in articles_raw]

    for d in decisions:
        idx = d.get("article_index")
        if not isinstance(idx, int) or not (0 <= idx < len(articles)):
            continue
        articles[idx].screening_decision = _decision_from_str(d.get("decision", "borderline"))
        articles[idx].screening_rationale = d.get("reason", "")
        articles[idx].screening_reason_category = d.get("reason_category")
        articles[idx].screening_pass1 = d.get("decision")

    screened_path.write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )

    pending_count = sum(1 for a in articles if a.screening_decision == ScreeningDecision.pending)
    complete = pending_count == 0

    summary: dict = {
        "batch_saved": len(decisions),
        "total_articles": len(articles),
        "pending_remaining": pending_count,
        "complete": complete,
    }

    if complete:
        counts: dict = {"include": 0, "exclude": 0, "borderline": 0, "by_reason": {}}
        for a in articles:
            dec = a.screening_decision.value
            if dec in counts:
                counts[dec] += 1
            if dec == "exclude":
                rc = a.screening_reason_category or "other"
                counts["by_reason"][rc] = counts["by_reason"].get(rc, 0) + 1

        eligibility = sum(
            1 for a in articles
            if a.screening_decision in (ScreeningDecision.include, ScreeningDecision.borderline)
        )
        review.status = ReviewStatus.abstract_screening
        review.prisma_counts.screened = len(articles)
        review.prisma_counts.excluded_screening = counts["by_reason"]
        review.prisma_counts.eligibility = eligibility
        state_manager.save(review, review_dir)

        summary.update({
            "include": counts["include"],
            "exclude": counts["exclude"],
            "borderline": counts["borderline"],
            "by_reason": counts["by_reason"],
        })

    print(prepare_output(summary))


async def screen_article_list(review, articles, mode=None):
    """Removed in Phase C2. Will be replaced in Phase C4 (iteration_manager conversion)."""
    raise NotImplementedError(
        "screen_article_list removed in Phase C2. "
        "Abstract screening now uses --prepare/--save CLI pattern."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Abstract screener — subscription mode")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--prepare", action="store_true")
    mode_group.add_argument("--save", action="store_true")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT, metavar="N")
    parser.add_argument("--batch-offset", type=int, default=0, metavar="N")
    parser.add_argument("--slice-of", type=int, default=None, metavar="N")
    parser.add_argument("--slice-index", type=int, default=None, metavar="I")
    parser.add_argument("--offset", type=int, default=0, metavar="N")
    parser.add_argument("--decisions-json", default=None, metavar="JSON")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)

    if args.prepare:
        prepare(review_dir, args.batch_size, args.batch_offset, args.slice_of, args.slice_index)
    else:
        if args.decisions_json is None:
            die("--decisions-json is required with --save.")
        save(review_dir, args.offset, args.decisions_json)
