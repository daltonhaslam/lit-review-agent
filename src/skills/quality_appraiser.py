"""Quality appraisal of included articles using validated instruments.

Detects study design from Article.study_design and selects the appropriate
quality instrument automatically:
  - AMSTAR-2       (16 items, 7 critical domains)  → systematic reviews / meta-analyses / umbrella / scoping reviews
  - RoB 2.0        (5 domains)                     → RCTs (incl. cluster, crossover)
  - STROBE         (22 items)                      → observational (cohort, case-control, cross-sectional)
  - COREQ          (32 items, 3 domains)           → qualitative studies
  - SANRA          (6 items, 0-2 each)             → narrative reviews / state-of-the-art reviews / policy reviews
  - Expert Opinion (transparency rubric)           → editorials / commentaries / letters / expert_opinion

Input:  review_dir (reads articles_included.json; status must be enriched)
Output: articles_included.json updated per batch; quality_report.md when complete;
        status=quality_appraised when complete
CLI:
  # Serial mode (single batch; advance --batch-offset across calls):
  python -m src.skills.quality_appraiser --review-dir reviews/{slug} --prepare \\
      --batch-size 50 --batch-offset 0
  # Parallel mode (one slice per parallel sub-agent):
  python -m src.skills.quality_appraiser --review-dir reviews/{slug} --prepare \\
      --slice-of N --slice-index I
  python -m src.skills.quality_appraiser --review-dir reviews/{slug} --save \\
      --offset 0 --input-json '{"appraisals": [...]}'
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.article import Article
from src.models.review import ReviewStatus
from src.skills._base import die, parse_save_input, prepare_output, slice_bounds, validate_slice_spec
from src.skills.data_extractor import _format_article_text

# ---------------------------------------------------------------------------
# Instrument detection
# ---------------------------------------------------------------------------

_SR_KEYWORDS = {"systematic review", "meta-analysis", "meta analysis", "umbrella review", "scoping review"}
# Non-RCT designs whose names contain "rct"/"randomized" but are NHMRC Level III-1
# (not true RCTs). Checked AHEAD of _RCT_KEYWORDS so they route to STROBE, not the
# RCT-only RoB 2.0 tool — mirrors the III-1-before-II guard in evidence_classifier.
_NON_RCT_KEYWORDS = {"quasi", "non randomized", "non randomised", "pseudo"}
_RCT_KEYWORDS = {"randomized", "randomised", "rct", "randomized controlled", "double-blind", "double blind"}
_QUAL_KEYWORDS = {"qualitative", "interview", "focus group", "ethnograph", "grounded theory", "thematic analysis", "phenomenolog"}
_NARRATIVE_KEYWORDS = {"narrative review", "narrative/clinical review", "narrative state", "policy review", "framework/methodology"}
_EXPERT_OPINION_KEYWORDS = {"editorial", "commentary", "expert_opinion", "expert opinion", "letter"}


def detect_instrument(study_design: str | None) -> str:
    if not study_design:
        return "STROBE"
    # Normalize canonical underscore IDs (e.g. systematic_review) to space form
    # so substring matches against keyword sets work consistently.
    sd = study_design.lower().replace("_", " ")
    if any(k in sd for k in _SR_KEYWORDS):
        return "AMSTAR-2"
    # Non-randomized designs first: "quasi rct"/"non randomized trial" contain
    # RCT tokens but are Level III-1, so they must not get the RCT-only RoB 2.0.
    if any(k in sd for k in _NON_RCT_KEYWORDS):
        return "STROBE"
    if any(k in sd for k in _RCT_KEYWORDS):
        return "RoB 2.0"
    if any(k in sd for k in _QUAL_KEYWORDS):
        return "COREQ"
    if any(k in sd for k in _NARRATIVE_KEYWORDS):
        return "SANRA"
    if any(k in sd for k in _EXPERT_OPINION_KEYWORDS):
        return "Expert Opinion"
    return "STROBE"


# ---------------------------------------------------------------------------
# Instrument prompts
# ---------------------------------------------------------------------------

# Appraisal criteria below paraphrase published instruments for prompting only;
# they are not verbatim reproductions. Sources & licenses:
#   AMSTAR-2 — Shea et al., BMJ 2017;358:j4008.
#   RoB 2    — Sterne et al., BMJ 2019;366:l4898 (Cochrane).
#   STROBE   — von Elm et al., PLoS Med 2007;4(10):e296 (STROBE Initiative).
#   COREQ    — Tong et al., Int J Qual Health Care 2007;19(6):349-357.
#   SANRA    — Baethge et al., Res Integr Peer Rev 2019;4:5 (CC BY 4.0).
# Users appraising studies should consult the original instruments.

_AMSTAR2_PROMPT = (
    "Appraise this systematic review using AMSTAR-2 (16 items, 7 critical domains).\n\n"
    "Critical domains (2, 4, 7, 9, 11, 13, 15):\n"
    "2. Duplicate study selection\n4. Comprehensive literature search\n7. List of excluded studies with justification\n"
    "9. Risk of bias assessment for individual studies\n11. Appropriate meta-analytic methods\n"
    "13. Consideration of risk of bias in interpretation\n15. Investigation and discussion of publication bias\n\n"
    "Rate each critical domain as Met / Not Met. Rate non-critical domains as Met / Partial / Not Met.\n\n"
    "Overall confidence: High (all critical met, ≤1 non-critical failed) | "
    "Moderate (all critical met, >1 non-critical failed) | "
    "Low (1 critical domain not met) | Critically Low (>1 critical not met)\n\n"
    "Scoring: 1.0=High, 0.75=Moderate, 0.5=Low, 0.25=Critically Low\n\n"
    'Respond with ONLY valid JSON: {"score": <0.0-1.0>, "confidence": "High|Moderate|Low|Critically Low", '
    '"critical_domains_met": <int 0-7>, "notes": "<2-3 sentences>"}'
)

_ROB2_PROMPT = (
    "Appraise this RCT using RoB 2.0 (5 domains).\n\n"
    "Domains:\n1. Bias from randomization process\n2. Bias due to deviations from interventions\n"
    "3. Bias due to missing outcome data\n4. Bias in outcome measurement\n5. Bias in selection of reported result\n\n"
    "Rate each domain: Low / Some concerns / High\n"
    "Overall: Low (all low) | Some concerns (≥1 some concerns, none high) | High (≥1 high)\n\n"
    "Scoring: 1.0=Low, 0.67=Some concerns, 0.33=High\n\n"
    'Respond with ONLY valid JSON: {"score": <0.0-1.0>, "overall": "Low|Some concerns|High", '
    '"domains_low_risk": <int 0-5>, "notes": "<2-3 sentences>"}'
)

_STROBE_PROMPT = (
    "Appraise this observational study using the STROBE checklist (22 items).\n\n"
    "Key items: title/abstract, background, objectives, study design, setting, participants, "
    "variables, data sources, bias, study size, quantitative variables, statistical methods, "
    "participants description, descriptive data, outcome data, main results, other analyses, "
    "key results, limitations, interpretation, generalisability, funding.\n\n"
    "Count how many of the 22 items are adequately reported. Score = items_met / 22.\n\n"
    'Respond with ONLY valid JSON: {"score": <0.0-1.0>, "items_met": <int 0-22>, "notes": "<2-3 sentences>"}'
)

_COREQ_PROMPT = (
    "Appraise this qualitative study using the COREQ checklist (32 items, 3 domains).\n\n"
    "Domain 1 — Research team and reflexivity (8 items): personal characteristics, relationship with participants\n"
    "Domain 2 — Study design (15 items): theoretical framework, sampling, data collection\n"
    "Domain 3 — Analysis and findings (9 items): coding, themes, quotations\n\n"
    "Count how many of the 32 items are adequately reported. Score = items_met / 32.\n\n"
    'Respond with ONLY valid JSON: {"score": <0.0-1.0>, "items_met": <int 0-32>, "notes": "<2-3 sentences>"}'
)

_SANRA_PROMPT = (
    "Appraise this narrative review using SANRA (Scale for the Assessment of Narrative Review Articles, 6 items).\n\n"
    "Items (each scored 0=not justified, 1=partial, 2=fully justified):\n"
    "1. Justification of the article's importance for the readership\n"
    "2. Statement of concrete aims or formulation of questions\n"
    "3. Description of the literature search\n"
    "4. Referencing (claims supported by appropriate citations)\n"
    "5. Scientific reasoning (e.g., appraisal of cited evidence quality)\n"
    "6. Appropriate presentation of data (figures/tables where relevant)\n\n"
    "Score = items_total / 12 (range 0.0–1.0). When the article is an abstract-only "
    "record, items 3 and 6 typically default to 0; reflect that in the score and note.\n\n"
    'Respond with ONLY valid JSON: {"score": <0.0-1.0>, "items_total": <int 0-12>, "notes": "<2-3 sentences>"}'
)

_EXPERT_OPINION_PROMPT = (
    "Appraise this editorial / commentary / letter / expert opinion piece using a transparency rubric. "
    "These are not original research — they are graded on how transparent and well-supported the argument is, "
    "not on study methodology.\n\n"
    "Items (each scored 0=absent, 1=partial, 2=clear):\n"
    "1. Author affiliation and credentials disclosed\n"
    "2. Conflict of interest / funding statement present\n"
    "3. Position or argument is clearly stated\n"
    "4. Argument is supported by citations to underlying evidence\n"
    "5. Counter-arguments or limitations acknowledged\n\n"
    "Score = items_total / 10 (range 0.0–1.0). Cap the maximum useful interpretation at "
    "Level IV evidence regardless of score — these articles do not constitute primary evidence.\n\n"
    'Respond with ONLY valid JSON: {"score": <0.0-1.0>, "items_total": <int 0-10>, "notes": "<2-3 sentences; flag this as expert opinion not primary evidence>"}'
)

_INSTRUMENT_PROMPTS: dict[str, str] = {
    "AMSTAR-2": _AMSTAR2_PROMPT,
    "RoB 2.0": _ROB2_PROMPT,
    "STROBE": _STROBE_PROMPT,
    "COREQ": _COREQ_PROMPT,
    "SANRA": _SANRA_PROMPT,
    "Expert Opinion": _EXPERT_OPINION_PROMPT,
}

_APPRAISAL_TASK = (
    "For each article in `articles`, apply the quality appraisal instrument specified in its `appraisal_prompt`. "
    "Assess based on `article_text` (title, authors, year, journal, abstract). "
    "Return ONLY valid JSON:\n"
    '{"appraisals": [{"orig_idx": N, "score": <0.0-1.0>, "notes": "<2-3 sentence quality summary>"}]}'
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _apply_appraisal(article: Article, instrument: str, data: dict) -> None:
    article.quality_instrument = instrument
    score = data.get("score")
    if score is not None:
        try:
            article.quality_score = max(0.0, min(1.0, float(score)))
        except (TypeError, ValueError):
            pass
    # Only overwrite notes when the payload actually carries them. save() stamps
    # every in-range index even without an appraisal entry, so an empty/partial
    # payload (e.g. /lit-review-update re-appraisal) must not wipe prior notes.
    notes = data.get("notes") or data.get("confidence")
    if notes:
        article.quality_notes = notes


def _write_quality_report(articles: list[Article], output_path: Path) -> None:
    lines = ["# Quality Appraisal Report\n"]
    by_instrument: dict[str, list[Article]] = {}
    for a in articles:
        inst = a.quality_instrument or "Unknown"
        by_instrument.setdefault(inst, []).append(a)

    for inst, arts in sorted(by_instrument.items()):
        lines.append(f"## {inst} (n={len(arts)})\n")
        lines.append("| Title | Year | Score | Notes |")
        lines.append("|---|---|---|---|")
        for a in arts:
            score_str = f"{a.quality_score:.2f}" if a.quality_score is not None else "—"
            notes = (a.quality_notes or "")[:100]
            title_short = a.title[:60] + "..." if len(a.title) > 60 else a.title
            lines.append(f"| {title_short} | {a.year or '—'} | {score_str} | {notes} |")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def _load_articles(review_dir: Path, review) -> tuple[Path, list[Article]]:
    included_path = review_dir / review.articles_included_file
    articles = [Article(**a) for a in json.loads(included_path.read_text())]
    return included_path, articles


# ---------------------------------------------------------------------------
# --prepare / --save
# ---------------------------------------------------------------------------

BATCH_SIZE_DEFAULT = 50


def prepare(
    review_dir: Path,
    batch_size: int = BATCH_SIZE_DEFAULT,
    batch_offset: int = 0,
    slice_of: int | None = None,
    slice_index: int | None = None,
) -> None:
    review = state_manager.load(review_dir)
    if review.status != ReviewStatus.enriched:
        die(f"Expected status 'enriched', got '{review.status}'.")
    validate_slice_spec(slice_of, slice_index)

    _, articles = _load_articles(review_dir, review)
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
    output_articles = []
    for i, a in enumerate(batch_articles):
        instrument = detect_instrument(a.study_design)
        output_articles.append({
            "orig_idx": start + i,
            "instrument": instrument,
            "article_text": _format_article_text(a, max_abstract=2000, include_sample_size=True),
            "appraisal_prompt": _INSTRUMENT_PROMPTS[instrument],
        })

    output: dict = {
        "batch_number": batch_number,
        "total_batches": total_batches,
        "batch_offset": start,
        "total_articles": total,
        "articles": output_articles,
        "appraisal_task": _APPRAISAL_TASK,
    }
    if slice_of is not None:
        output["slice_of"] = slice_of
        output["slice_index"] = slice_index
        output["slice_pending"] = len(batch_articles)
        output["slice_indices"] = list(range(start, end))
    print(prepare_output(output))


def _quality_stats(articles: list[Article]) -> dict:
    """Compute quality-score distribution stats across all articles with a score."""
    scored = [a.quality_score for a in articles if a.quality_score is not None]
    if not scored:
        return {
            "low_quality_count": 0,
            "mean_quality": 0.0,
            "median_quality": 0.0,
            "min_quality": 0.0,
            "max_quality": 0.0,
            "by_instrument_stats": {},
        }
    by_inst: dict[str, list[float]] = {}
    for a in articles:
        if a.quality_score is None or not a.quality_instrument:
            continue
        by_inst.setdefault(a.quality_instrument, []).append(a.quality_score)
    return {
        "low_quality_count": sum(1 for s in scored if s < 0.5),
        "mean_quality": round(statistics.mean(scored), 3),
        "median_quality": round(statistics.median(scored), 3),
        "min_quality": round(min(scored), 3),
        "max_quality": round(max(scored), 3),
        "by_instrument_stats": {
            inst: {
                "count": len(scores),
                "median": round(statistics.median(scores), 3),
                "min": round(min(scores), 3),
                "max": round(max(scores), 3),
            }
            for inst, scores in by_inst.items()
        },
    }


def save(
    review_dir: Path,
    input_json: str,
    batch_size: int = BATCH_SIZE_DEFAULT,
    batch_offset: int = 0,
) -> None:
    try:
        data = parse_save_input(input_json)
    except ValueError as exc:
        die(str(exc))

    review = state_manager.load(review_dir)
    included_path, articles = _load_articles(review_dir, review)
    total = len(articles)

    appraisals_by_idx = {
        ap["orig_idx"]: ap
        for ap in data.get("appraisals", [])
        if isinstance(ap.get("orig_idx"), int)
    }

    # Stamp instrument for every article in the batch range even without an
    # appraisal entry (serial-mode quality_report grouping depends on it); also
    # apply appraisals at any merged-payload index outside that range.
    serial_range = range(batch_offset, min(batch_offset + batch_size, total))
    for idx in sorted(set(appraisals_by_idx) | set(serial_range)):
        if 0 <= idx < total:
            article = articles[idx]
            _apply_appraisal(article, detect_instrument(article.study_design), appraisals_by_idx.get(idx, {}))

    included_path.write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2),
        encoding="utf-8",
    )

    by_instrument: dict[str, int] = {}
    for a in articles:
        inst = a.quality_instrument
        if inst is not None:
            by_instrument[inst] = by_instrument.get(inst, 0) + 1

    full_coverage = total > 0 and all(i in appraisals_by_idx for i in range(total))
    complete = full_coverage or batch_offset + batch_size >= total
    if complete:
        _write_quality_report(articles, review_dir / "quality_report.md")
        review.status = ReviewStatus.quality_appraised
        state_manager.save(review, review_dir)

    stats = _quality_stats(articles)
    summary: dict = {
        "batch_saved": len(appraisals_by_idx),
        "total_articles": total,
        "complete": complete,
        "by_instrument": by_instrument,
        **stats,
    }
    if complete:
        summary["quality_report"] = "quality_report.md"
    print(prepare_output(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quality appraisal of included articles")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--save", action="store_true")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT, metavar="N")
    parser.add_argument("--batch-offset", type=int, default=0, metavar="N")
    parser.add_argument("--slice-of", type=int, default=None, metavar="N")
    parser.add_argument("--slice-index", type=int, default=None, metavar="I")
    parser.add_argument("--offset", type=int, default=0, metavar="N",
                        help="Batch start offset for --save (matches --batch-offset from --prepare)")
    parser.add_argument("--input-json", metavar="JSON")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)
    if args.prepare:
        prepare(review_dir, args.batch_size, args.batch_offset, args.slice_of, args.slice_index)
    else:
        if not args.input_json:
            die("--save requires --input-json")
        save(review_dir, args.input_json, args.batch_size, args.offset)
