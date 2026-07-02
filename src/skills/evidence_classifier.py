"""NHMRC evidence level classification for included articles.

Rule-based mapping from study_design → NHMRC Level I–IV.
Also flags surrogate outcomes when article primary_outcomes differ from PICO O.

NHMRC hierarchy:
  Level I    — Systematic reviews / meta-analyses of RCTs
  Level II   — RCTs (individual)
  Level III-1 — Pseudo-randomised / non-randomised trials
  Level III-2 — Comparative studies with concurrent controls (prospective cohort, controlled BA)
  Level III-3 — Comparative studies without concurrent controls (retrospective cohort, case-control)
  Level IV   — Case series, cross-sectional (descriptive), expert opinion, narrative review

No LLM calls. Pure rule-based classification from study_design string.

Input:  review_dir (reads articles_included.json; status must be quality_appraised)
Output: articles_included.json updated with evidence_level + surrogate_outcome_flag;
        status=evidence_classified
CLI:    python -m src.skills.evidence_classifier --review-dir reviews/{slug}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agent import state as state_manager
from src.models.article import Article, EvidenceLevel
from src.models.review import ReviewStatus
from src.skills._study_design import CANONICAL_TO_NHMRC

# ---------------------------------------------------------------------------
# Level detection rules (longest/most-specific match first)
# ---------------------------------------------------------------------------

_LEVEL_I_KEYWORDS = {
    "systematic review", "meta-analysis", "meta analysis",
    "umbrella review", "network meta", "systematic literature review",
}
_LEVEL_II_KEYWORDS = {
    "randomized controlled trial", "randomised controlled trial",
    "randomized trial", "randomised trial",
    "double-blind", "double blind", "double-masked",
    "rct", "single-blind", "single blind",
    "cluster randomized", "cluster randomised",
    "stepped wedge",
}
_LEVEL_III1_KEYWORDS = {
    "pseudo-randomized", "pseudo randomized", "quasi-randomized", "quasi randomized",
    "non-randomized trial", "non-randomised trial", "non randomized trial",
    "controlled trial without randomization",
    "before-after study with control", "controlled before-after",
    "interrupted time series",
}
_LEVEL_III2_KEYWORDS = {
    "prospective cohort", "prospective study",
    "comparative study", "controlled study",
    "concurrent control", "parallel group",
}
_LEVEL_III3_KEYWORDS = {
    "retrospective cohort", "cohort study",
    "case-control", "case control",
    "nested case-control",
    "historical control",
}
_LEVEL_IV_KEYWORDS = {
    "case series", "case report",
    "cross-sectional", "cross sectional",
    "descriptive study",
    "narrative review", "expert opinion", "consensus",
    "editorial", "commentary", "letter",
    "scoping review",
}


def classify_level(study_design: str | None) -> EvidenceLevel:
    if not study_design:
        return EvidenceLevel.unknown
    sd = study_design.lower()
    # Direct canonical match; falls through to substring scan for legacy free-text.
    if sd in CANONICAL_TO_NHMRC:
        return CANONICAL_TO_NHMRC[sd]
    if any(k in sd for k in _LEVEL_I_KEYWORDS):
        return EvidenceLevel.level_I
    # III-1 checked before II: quasi/pseudo-randomized contains "randomized" but is not RCT
    if any(k in sd for k in _LEVEL_III1_KEYWORDS):
        return EvidenceLevel.level_III_1
    if any(k in sd for k in _LEVEL_II_KEYWORDS):
        return EvidenceLevel.level_II
    if any(k in sd for k in _LEVEL_III2_KEYWORDS):
        return EvidenceLevel.level_III_2
    if any(k in sd for k in _LEVEL_III3_KEYWORDS):
        return EvidenceLevel.level_III_3
    if any(k in sd for k in _LEVEL_IV_KEYWORDS):
        return EvidenceLevel.level_IV
    return EvidenceLevel.unknown


_STOPWORDS = {"a", "an", "the", "of", "in", "and", "or", "for", "to", "from", "with", "at", "all"}


def _content_words(text: str) -> set[str]:
    return {w for w in text.lower().split() if w not in _STOPWORDS and len(w) > 2}


def _is_surrogate_outcome(
    article_outcomes: list[str], pico_outcomes: list[str]
) -> bool | None:
    """Flag when article's primary outcomes share no content words with PICO-specified outcomes.

    Returns None (not False) when either list is empty — empty primary_outcomes means
    outcome alignment is unknown, not confirmed non-surrogate.
    Word-level overlap handles paraphrasing like '30-day all-cause readmission' matching
    '30-day readmission'.
    """
    if not article_outcomes or not pico_outcomes:
        return None
    pico_word_sets = [_content_words(o) for o in pico_outcomes]
    for article_outcome in article_outcomes:
        ao_words = _content_words(article_outcome)
        for pico_words in pico_word_sets:
            if ao_words & pico_words:
                return False
    return True


def classify(review_dir: Path) -> dict:
    review = state_manager.load(review_dir)
    if review.status != ReviewStatus.quality_appraised:
        raise ValueError(f"Expected status 'quality_appraised', got '{review.status}'.")

    pico_outcomes = review.pico.O if review.pico else []

    included_path = review_dir / review.articles_included_file
    articles = [Article(**a) for a in json.loads(included_path.read_text())]

    level_counts: dict[str, int] = {}
    surrogate_flagged = 0

    for article in articles:
        level = classify_level(article.study_design)
        article.evidence_level = level
        level_counts[level.value] = level_counts.get(level.value, 0) + 1

        result = _is_surrogate_outcome(article.primary_outcomes, pico_outcomes)
        article.surrogate_outcome_flag = result
        if result is True:
            surrogate_flagged += 1

    included_path.write_text(json.dumps([a.model_dump(mode="json") for a in articles], indent=2))

    review.status = ReviewStatus.evidence_classified
    state_manager.save(review, review_dir)

    return {
        "classified": len(articles),
        "by_level": level_counts,
        "surrogate_flagged": surrogate_flagged,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classify NHMRC evidence level for included articles")
    parser.add_argument("--review-dir", required=True, metavar="DIR")
    args = parser.parse_args()

    try:
        result = classify(Path(args.review_dir))
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
