"""Narrative synthesis of the included evidence base.

--prepare: loads articles, builds synthesis context for Claude to analyze.
--save:    accepts Claude's synthesis JSON, writes synthesis.json; status=synthesized.

Input:  review_dir (reads articles_included.json; status must be gap_analyzed)
Output: synthesis.json; status=synthesized
CLI:
  python -m src.skills.synthesizer --review-dir reviews/{slug} --prepare
  python -m src.skills.synthesizer --review-dir reviews/{slug} --save --input-json '{...}'
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.article import Article, EvidenceLevel, ExtractionSource, ScreeningDecision
from src.models.review import ReviewStatus
from src.skills._base import die, parse_save_input, prepare_output
from src.skills.iteration_manager import _fingerprint

_SYNTHESIS_TASK_BASE = (
    "You are an academic-physician-grade systematic review synthesizer. "
    "Synthesize the evidence narratively by PICO outcome. "
    "Use author-year inline citations (e.g., Smith 2022, Jones 2023). "
    "Be specific about effect sizes, study populations, and limitations. "
    "Do not inflate the evidence — acknowledge uncertainty explicitly.\n\n"
    "Each article block lists a `Source:` line indicating whether the data was extracted "
    "from full text or abstract only. Treat abstract-only sources as weaker evidence: "
    "abstracts can omit or misstate methods, effect sizes, and limitations relative to the "
    "full paper. When a key finding rests primarily on abstract-only sources, note this in "
    "`evidence_quality_summary` or `limitations`.\n\n"
    "Respond with ONLY valid JSON:\n"
    '{"by_outcome": [{"outcome": "<PICO outcome>", "narrative": "<paragraph>", '
    '"highest_level": "<NHMRC level>", "article_count": <int>}], '
    '"overall_narrative": "<2-3 paragraph summary across all outcomes>", '
    '"key_findings": ["<bullet>"], '
    '"evidence_quality_summary": "<paragraph>", '
    '"limitations": ["<limitation>"]}'
)

_SYNTHESIS_TASK_SUPPLEMENT = (
    "\n\nABSTRACT SUPPLEMENT — IMPORTANT: The input includes an `abstract_supplement` field "
    "listing articles that passed Stage 8 screening but had no full-text available, so they "
    "were never appraised at full-text. Do NOT cite or fold these articles into `by_outcome`, "
    "`overall_narrative`, `key_findings`, or `evidence_quality_summary` — they are not part "
    "of the appraised evidence base. Instead, add ONE additional top-level field to your JSON "
    'response: `"abstract_supplement_summary": "<1-2 paragraphs>"`. Describe these articles '
    "as supplemental evidence using author-year citations, explicitly note that they were not "
    "full-text appraised, and warn that abstracts can misrepresent results once the full text "
    "is read. If `abstract_supplement` is empty or absent, omit `abstract_supplement_summary` "
    "entirely."
)


def _synthesis_task(supplement_present: bool) -> str:
    return _SYNTHESIS_TASK_BASE + (_SYNTHESIS_TASK_SUPPLEMENT if supplement_present else "")


def _author_year(article: Article) -> str:
    # A non-empty authors list can still hold an empty/whitespace name
    # (hand-edited / imported records), so guard against parts == [].
    parts = article.authors[0].strip().split() if article.authors else []
    first_author = parts[0] if parts else "Unknown"
    return f"{first_author} {article.year or '?'}"


_SOURCE_LABELS: dict[ExtractionSource, str] = {
    ExtractionSource.full_text: "full text",
    ExtractionSource.abstract: "abstract only",
    ExtractionSource.tldr: "TLDR only",
    ExtractionSource.none: "no body text",
}


def _build_synthesis_context(articles: list[Article]) -> str:
    parts = []
    for i, a in enumerate(articles, 1):
        cite = _author_year(a)
        level = a.evidence_level.value if a.evidence_level else "unknown"
        quality = f"{a.quality_score:.2f}" if a.quality_score is not None else "?"
        design = a.study_design or "unknown"
        n = f"N={a.sample_size}" if a.sample_size else ""
        follow_up = a.follow_up_duration or ""
        outcomes = "; ".join(a.primary_outcomes[:3]) if a.primary_outcomes else "not extracted"
        effect = a.effect_sizes or "not reported"
        conclusions = a.author_conclusions or a.tldr or ""
        intervention = a.intervention_details or ""
        quality_notes = a.quality_notes or ""
        surrogate_note = " [SURROGATE OUTCOME — interpret with caution]" if a.surrogate_outcome_flag is True else ""
        source_label = _SOURCE_LABELS.get(a.extraction_source, "unknown source") if a.extraction_source else "unknown source"

        block = (
            f"[{i}] {cite} ({level}, quality={quality}){surrogate_note}\n"
            f"  Title: {a.title}\n"
            f"  Source: {source_label}\n"
            f"  Design: {design}" + (f", {n}" if n else "") + (f", follow-up: {follow_up}" if follow_up else "") + "\n"
        )
        if intervention:
            block += f"  Intervention: {intervention[:150]}\n"
        block += f"  Primary outcomes: {outcomes}\n"
        block += f"  Effect sizes: {effect}\n"
        if conclusions:
            block += f"  Conclusions: {conclusions[:300]}"
        if quality_notes:
            block += f"\n  Quality notes: {quality_notes[:200]}"
        parts.append(block)
    return "\n\n".join(parts)


_LEVEL_ORDER = [
    EvidenceLevel.level_I,
    EvidenceLevel.level_II,
    EvidenceLevel.level_III_1,
    EvidenceLevel.level_III_2,
    EvidenceLevel.level_III_3,
    EvidenceLevel.level_IV,
    EvidenceLevel.unknown,
]
_LEVEL_LABELS = {
    EvidenceLevel.level_I: "SR/MA",
    EvidenceLevel.level_II: "RCT",
    EvidenceLevel.level_III_1: "Non-RCT",
    EvidenceLevel.level_III_2: "Prospective cohort",
    EvidenceLevel.level_III_3: "Retrospective",
    EvidenceLevel.level_IV: "Case series/other",
    EvidenceLevel.unknown: "Unknown",
}


def _build_evidence_map(articles: list[Article], pico_outcomes: list[str]) -> list[dict]:
    raw_counts = Counter(a.evidence_level for a in articles)
    level_counts = {
        _LEVEL_LABELS[level]: raw_counts[level]
        for level in _LEVEL_ORDER
        if raw_counts[level]
    }
    total = len(articles)
    rows = [
        {"outcome": outcome, "by_design": level_counts.copy(), "total": total}
        for outcome in pico_outcomes
    ]
    return rows


def _build_quantitative_scaffold(articles: list[Article]) -> list[dict]:
    rows = []
    for a in articles:
        if not a.effect_sizes:
            continue
        rows.append({
            "cite": _author_year(a),
            "design": a.study_design or "?",
            "n": a.sample_size,
            "outcomes": a.primary_outcomes[:2],
            "effect_sizes": a.effect_sizes,
            "evidence_level": a.evidence_level.value if a.evidence_level else "unknown",
            "quality_score": a.quality_score,
            "surrogate": a.surrogate_outcome_flag,
        })
    return rows


_ABSTRACT_ONLY_DECISIONS = {ScreeningDecision.include, ScreeningDecision.borderline}


def _load_abstract_supplement(review_dir: Path, screened_file: str, included: list[Article]) -> list[dict]:
    """Articles that cleared screening (include/borderline) but never reached articles_included.json.

    Why: typically no PDF was available via the OA waterfall (library_request.csv leftovers),
    so full-text appraisal never ran. Returned as a parallel evidence stream that the
    synthesizer must label non-appraised.
    """
    screened_path = review_dir / screened_file
    if not screened_path.exists():
        die(f"{screened_file} missing despite review status guaranteeing screening completed.")
    included_keys = {_fingerprint(a) for a in included}
    supplement = []
    for raw in json.loads(screened_path.read_text()):
        article = Article(**raw)
        if article.screening_decision not in _ABSTRACT_ONLY_DECISIONS:
            continue
        if _fingerprint(article) in included_keys:
            continue
        supplement.append({
            "cite": _author_year(article),
            "title": article.title,
            "doi": article.doi,
            "pmid": article.pmid,
            "screening_decision": article.screening_decision.value,
            "abstract": article.abstract or "",
            "evidence_basis": "abstract_only",
        })
    return supplement


def _count_abstract_supplement(review_dir: Path) -> int:
    review = state_manager.load(review_dir)
    included_path = review_dir / review.articles_included_file
    included = [Article(**a) for a in json.loads(included_path.read_text())] if included_path.exists() else []
    return len(_load_abstract_supplement(review_dir, review.articles_screened_file, included))


def _data_completeness(articles: list[Article]) -> tuple[dict, bool]:
    total = len(articles)
    with_design = with_quality = total_cc = cc_with_design = 0
    missing_authors = missing_year = 0
    for a in articles:
        if a.study_design:
            with_design += 1
        if a.quality_score is not None:
            with_quality += 1
        if a.citation_chased:
            total_cc += 1
            if a.study_design:
                cc_with_design += 1
        # Inline citations require authors + year; journal/abstract are
        # checked separately by citation_chaser._is_metadata_complete.
        if not a.authors:
            missing_authors += 1
        if not a.year:
            missing_year += 1
    cc_incomplete = total_cc - cc_with_design

    stats = {
        "total": total,
        "with_study_design": with_design,
        "with_quality_score": with_quality,
        "pct_with_study_design": round(with_design / total, 3) if total else 0.0,
        "pct_with_quality_score": round(with_quality / total, 3) if total else 0.0,
        "total_citation_chased": total_cc,
        "cc_with_study_design": cc_with_design,
        "cc_incomplete": cc_incomplete,
        "pct_citation_chased_complete": round(cc_with_design / total_cc, 3) if total_cc else None,
        # Issue #50 — author-year inline citations require these fields.
        "total_missing_authors": missing_authors,
        "total_missing_year": missing_year,
        "pct_missing_authors": round(missing_authors / total, 3) if total else 0.0,
        "pct_missing_year": round(missing_year / total, 3) if total else 0.0,
    }
    warning = cc_incomplete > 0 or missing_authors > 0 or missing_year > 0
    return stats, warning


def prepare(review_dir: Path, include_abstract_supplement: bool = False) -> None:
    review = state_manager.load(review_dir)
    if review.status != ReviewStatus.gap_analyzed:
        die(f"Expected status 'gap_analyzed', got '{review.status}'.")

    included_path = review_dir / review.articles_included_file
    articles = [Article(**a) for a in json.loads(included_path.read_text())]

    pico = review.pico
    pico_outcomes = pico.O if pico else []
    pico_text = (
        f"P: {pico.P}\nI: {pico.I}\nC: {pico.C}\n"
        f"O: {', '.join(pico.O)}\n"
        + (f"S: {pico.S}" if pico.S else "")
        if pico else "PICO not defined"
    )

    completeness, warning = _data_completeness(articles)

    supplement: list[dict] = []
    if include_abstract_supplement:
        supplement = _load_abstract_supplement(review_dir, review.articles_screened_file, articles)

    output = {
        "review_id": review.review_id,
        "review_question": review.raw_question,
        "pico_text": pico_text,
        "pico_outcomes": pico_outcomes,
        "total_articles": len(articles),
        "data_completeness": completeness,
        "data_completeness_warning": warning,
        "synthesis_context": _build_synthesis_context(articles),
        "synthesis_task": _synthesis_task(bool(supplement)),
    }
    if supplement:
        output["abstract_supplement"] = supplement
    print(prepare_output(output))


def save(review_dir: Path, input_json: str) -> None:
    data = parse_save_input(input_json)
    review = state_manager.load(review_dir)

    included_path = review_dir / review.articles_included_file
    articles = [Article(**a) for a in json.loads(included_path.read_text())]
    pico_outcomes = review.pico.O if review.pico else []

    abstract_supplement_summary = data.pop("abstract_supplement_summary", None)

    synthesis = {
        "review_id": review.review_id,
        "question": review.raw_question,
        "total_included": len(articles),
        "narrative": data,
        "evidence_map": _build_evidence_map(articles, pico_outcomes),
        "quantitative_scaffold": _build_quantitative_scaffold(articles),
    }
    if abstract_supplement_summary:
        # Schema coupling: output_formatter.save reads this key to render the executive-summary appendix.
        synthesis["abstract_supplement"] = abstract_supplement_summary
    (review_dir / "synthesis.json").write_text(json.dumps(synthesis, indent=2), encoding="utf-8")

    review.status = ReviewStatus.synthesized
    state_manager.save(review, review_dir)

    print(prepare_output({
        "articles_synthesized": len(articles),
        "outcomes_addressed": len(data.get("by_outcome", [])),
        "key_findings": len(data.get("key_findings", [])),
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synthesize evidence from included articles")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    parser.add_argument("--prepare", dest="mode_prepare", action="store_true")
    parser.add_argument("--save", dest="mode_save", action="store_true")
    parser.add_argument("--count-abstract-only", dest="mode_count", action="store_true",
                        help="Print {abstract_only_candidate_count: N} and exit; cheap probe for Stage 16 opt-in.")
    parser.add_argument("--input-json", default="")
    parser.add_argument("--include-abstract-supplement", dest="include_abstract_supplement", action="store_true")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)
    try:
        if args.mode_prepare:
            prepare(review_dir, include_abstract_supplement=args.include_abstract_supplement)
        elif args.mode_save:
            save(review_dir, args.input_json)
        elif args.mode_count:
            print(prepare_output({"abstract_only_candidate_count": _count_abstract_supplement(review_dir)}))
        else:
            die("Specify --prepare, --save, or --count-abstract-only")
    except SystemExit:
        raise
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
