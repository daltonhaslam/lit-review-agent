"""Gap analysis for the included article set.

Prepares evidence summary for Claude to identify coverage gaps across PICO dimensions,
outcome coverage, population scope, recency, and study design variety. Saves gap_analysis.md
and gap_analysis.json from Claude's output.

Input:  review_dir (reads articles_included.json + state; status must be evidence_classified)
Output: gap_analysis.md; gap_analysis.json; state updated with status=gap_analyzed
CLI:
  python -m src.skills.gap_analyzer --review-dir reviews/{slug} --prepare
  python -m src.skills.gap_analyzer --review-dir reviews/{slug} --save --input-json '{"pico_gaps": [...], ...}'
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.article import Article, EvidenceLevel
from src.models.review import Review, ReviewStatus
from src.skills._base import die, parse_save_input, prepare_output
from src.skills.abstract_screener import _build_pico_context

_CURRENT_YEAR = datetime.now(timezone.utc).year
_RECENCY_THRESHOLD_YEARS = 5

_GAP_TASK = (
    "You are a systematic review methodologist performing gap analysis on a completed literature search. "
    "Review the article summary, evidence statistics, and PICO framework provided. "
    "Identify specific, actionable gaps — not generic observations.\n\n"
    "Respond with ONLY valid JSON matching this schema:\n"
    '{"pico_gaps": [{"aspect": "P|I|C|O|S", "gap": "<description>", "severity": "high|medium|low"}], '
    '"outcome_gaps": ["<outcome not addressed>"], '
    '"population_gaps": ["<subpopulation not covered>"], '
    '"recency_gaps": "<string or null>", '
    '"design_gaps": ["<study design missing that would strengthen evidence>"], '
    '"suggested_queries": [{"target_gap": "<gap>", "query": "<PubMed query string>", "rationale": "<why>"}], '
    '"recommendation": "iterate|stop", '
    '"recommendation_rationale": "<1-2 sentences>"}'
)


# ---------------------------------------------------------------------------
# Pure helpers (unchanged)
# ---------------------------------------------------------------------------

def _build_article_summary(articles: list[Article]) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        level = a.evidence_level.value if a.evidence_level else "unknown"
        surrogate = " [SURROGATE OUTCOME]" if a.surrogate_outcome_flag else ""
        design = a.study_design or "unknown design"
        outcomes = "; ".join(a.primary_outcomes[:3]) if a.primary_outcomes else "not extracted"
        population_notes = f"N={a.sample_size}" if a.sample_size else ""
        year = a.year or "?"
        lines.append(
            f"{i}. ({year}, {level}, {design}{', ' + population_notes if population_notes else ''}){surrogate}\n"
            f"   Title: {a.title}\n"
            f"   Outcomes: {outcomes}"
        )
    return "\n".join(lines)


def _build_evidence_map(articles: list[Article]) -> dict[str, dict[str, int]]:
    design_bucket = {
        EvidenceLevel.level_I: "Systematic review / meta-analysis",
        EvidenceLevel.level_II: "RCT",
        EvidenceLevel.level_III_1: "Non-randomized trial",
        EvidenceLevel.level_III_2: "Prospective cohort",
        EvidenceLevel.level_III_3: "Retrospective cohort / case-control",
        EvidenceLevel.level_IV: "Case series / expert opinion",
        EvidenceLevel.unknown: "Unknown",
    }
    outcome_map: dict[str, Counter] = {}
    for a in articles:
        bucket = design_bucket.get(a.evidence_level, "Unknown")
        for outcome in a.primary_outcomes:
            if outcome not in outcome_map:
                outcome_map[outcome] = Counter()
            outcome_map[outcome][bucket] += 1
    return {outcome: dict(counter) for outcome, counter in outcome_map.items()}


def _recency_stats(articles: list[Article]) -> dict:
    years = [a.year for a in articles if a.year]
    if not years:
        return {"oldest": None, "newest": None, "recent_count": 0, "total": len(articles)}
    recent_cutoff = _CURRENT_YEAR - _RECENCY_THRESHOLD_YEARS
    return {
        "oldest": min(years),
        "newest": max(years),
        "recent_count": sum(1 for y in years if y >= recent_cutoff),
        "total": len(articles),
    }


def _evidence_level_distribution(articles: list[Article]) -> dict[str, int]:
    counts: dict[str, int] = Counter()
    for a in articles:
        level = a.evidence_level.value if a.evidence_level else "unknown"
        counts[level] += 1
    return dict(counts)


def _study_design_distribution(articles: list[Article]) -> dict[str, int]:
    counts: dict[str, int] = Counter()
    for a in articles:
        counts[a.study_design or "unknown"] += 1
    return dict(counts)


def _write_gap_analysis(
    review: Review,
    articles: list[Article],
    gap_data: dict,
    output_path: Path,
    *,
    recency: dict,
    evidence_level_dist: dict[str, int],
    study_design_dist: dict[str, int],
    evidence_map: dict[str, dict[str, int]],
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        "# Gap Analysis",
        "",
        f"**Review:** {review.raw_question}",
        f"**Date:** {now}",
        f"**Included articles:** {len(articles)}",
        "",
        "---",
        "",
        "## Evidence Map",
        "",
    ]

    if evidence_map:
        all_designs = sorted({d for counts in evidence_map.values() for d in counts})
        header = "| Outcome | " + " | ".join(all_designs) + " |"
        sep = "|---|" + "---|" * len(all_designs)
        lines += [header, sep]
        for outcome, counts in list(evidence_map.items())[:10]:
            cells = [str(counts.get(d, 0)) for d in all_designs]
            lines.append(f"| {outcome[:60]} | " + " | ".join(cells) + " |")
        lines.append("")
    else:
        lines += ["*(No outcome data available — data extraction may not have run)*", ""]

    lines += ["## Evidence Level Distribution", ""]
    for level, count in sorted(evidence_level_dist.items()):
        lines.append(f"- Level {level}: {count} article(s)")
    lines += ["", "## Study Design Distribution", ""]
    for design, count in sorted(study_design_dist.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {design}: {count} article(s)")

    lines += [
        "",
        "## Recency",
        "",
        f"- Oldest included article: {recency['oldest'] or 'unknown'}",
        f"- Newest included article: {recency['newest'] or 'unknown'}",
        f"- Within last {_RECENCY_THRESHOLD_YEARS} years: {recency['recent_count']}/{recency['total']}",
        "",
        "---",
        "",
        "## Identified Gaps",
        "",
    ]

    pico_gaps = gap_data.get("pico_gaps", [])
    if pico_gaps:
        lines.append("### PICO Coverage Gaps")
        lines.append("")
        for g in pico_gaps:
            severity = g.get("severity", "")
            badge = {"high": "HIGH", "medium": "MED", "low": "LOW"}.get(severity, "")
            lines.append(f"- **[{badge}] {g.get('aspect', '?')}:** {g.get('gap', '')}")
        lines.append("")

    outcome_gaps = gap_data.get("outcome_gaps", [])
    if outcome_gaps:
        lines.append("### Outcome Gaps")
        lines.append("")
        for o in outcome_gaps:
            lines.append(f"- {o}")
        lines.append("")

    population_gaps = gap_data.get("population_gaps", [])
    if population_gaps:
        lines.append("### Population Gaps")
        lines.append("")
        for p in population_gaps:
            lines.append(f"- {p}")
        lines.append("")

    recency_gap = gap_data.get("recency_gaps")
    if recency_gap:
        lines += ["### Recency Gap", "", recency_gap, ""]

    design_gaps = gap_data.get("design_gaps", [])
    if design_gaps:
        lines.append("### Study Design Gaps")
        lines.append("")
        for d in design_gaps:
            lines.append(f"- {d}")
        lines.append("")

    suggested_queries = gap_data.get("suggested_queries", [])
    if suggested_queries:
        lines += ["---", "", "## Suggested Search Queries (for next iteration)", ""]
        for q in suggested_queries:
            lines.append(f"**Gap:** {q.get('target_gap', '')}")
            lines.append(f"**Query:** `{q.get('query', '')}`")
            lines.append(f"**Rationale:** {q.get('rationale', '')}")
            lines.append("")

    # `.get(key, default)` returns None (not the default) when the LLM emits
    # `"recommendation": null`; coerce so rec.upper() below can't crash save().
    rec = gap_data.get("recommendation") or "stop"
    rec_rationale = gap_data.get("recommendation_rationale", "")
    lines += [
        "---",
        "",
        "## Recommendation",
        "",
        f"**{rec.upper()}** — {rec_rationale}",
        "",
    ]

    output_path.write_text("\n".join(lines), encoding="utf-8")


def _load_articles(review_dir: Path, review: Review) -> list[Article]:
    included_path = review_dir / review.articles_included_file
    return [Article(**a) for a in json.loads(included_path.read_text())]



# ---------------------------------------------------------------------------
# --prepare / --save
# ---------------------------------------------------------------------------

def prepare(review_dir: Path) -> None:
    review = state_manager.load(review_dir)
    if review.status != ReviewStatus.evidence_classified:
        die(f"Expected status 'evidence_classified', got '{review.status}'.")

    articles = _load_articles(review_dir, review)
    recency = _recency_stats(articles)
    evidence_level_dist = _evidence_level_distribution(articles)
    study_design_dist = _study_design_distribution(articles)

    print(prepare_output({
        "review_question": review.raw_question,
        "pico_text": _build_pico_context(review),
        "total_included": len(articles),
        "article_summary": _build_article_summary(articles),
        "evidence_level_distribution": evidence_level_dist,
        "study_design_distribution": study_design_dist,
        "recency": recency,
        "recency_threshold_years": _RECENCY_THRESHOLD_YEARS,
        "gap_task": _GAP_TASK,
    }))


def save(review_dir: Path, input_json: str) -> None:
    try:
        gap_data = parse_save_input(input_json)
    except ValueError as exc:
        die(str(exc))

    review = state_manager.load(review_dir)
    articles = _load_articles(review_dir, review)

    recency = _recency_stats(articles)
    evidence_level_dist = _evidence_level_distribution(articles)
    study_design_dist = _study_design_distribution(articles)
    evidence_map = _build_evidence_map(articles)

    _write_gap_analysis(
        review, articles, gap_data,
        review_dir / "gap_analysis.md",
        recency=recency, evidence_level_dist=evidence_level_dist,
        study_design_dist=study_design_dist, evidence_map=evidence_map,
    )
    (review_dir / "gap_analysis.json").write_text(
        json.dumps(gap_data, indent=2), encoding="utf-8"
    )

    review.status = ReviewStatus.gap_analyzed
    state_manager.save(review, review_dir)

    print(prepare_output({
        "articles_analyzed": len(articles),
        "pico_gaps": len(gap_data.get("pico_gaps", [])),
        "suggested_queries": len(gap_data.get("suggested_queries", [])),
        "recommendation": gap_data.get("recommendation", "stop"),
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Identify evidence gaps in included article set")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--save", action="store_true")
    parser.add_argument("--input-json", metavar="JSON")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)
    if args.prepare:
        prepare(review_dir)
    else:
        if not args.input_json:
            die("--save requires --input-json")
        save(review_dir, args.input_json)
