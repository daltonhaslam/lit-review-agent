"""Study-design controlled vocabulary + canonicalizer.

Shared by `data_extractor` (Stage 11), `fulltext_assessor` (Stage 10), and
`evidence_classifier` (Stage 14). Lives in its own module so the two extractor
stages can import from a common ancestor without creating a cycle
(`data_extractor` already imports `FULLTEXT_MAX_CHARS` from `fulltext_assessor`).
"""
from __future__ import annotations

from src.models.article import Article, EvidenceLevel

# Controlled vocabulary for `study_design`. Listed in NHMRC level order.
# Sub-agents pick the closest term from this list; legacy free-text values
# from older reviews are normalized via canonicalize_study_design.
CANONICAL_STUDY_DESIGNS: tuple[str, ...] = (
    # Level I
    "systematic_review", "meta_analysis", "umbrella_review",
    # Level II
    "rct", "cluster_rct",
    # Level III-1
    "quasi_rct", "non_randomized_trial", "controlled_before_after", "interrupted_time_series",
    # Level III-2
    "prospective_cohort", "comparative_study",
    # Level III-3
    "retrospective_cohort", "case_control", "historical_control",
    # Level IV (descriptive, qualitative, opinion, methodology-light)
    "case_series", "case_report", "cross_sectional", "qualitative", "mixed_methods",
    "narrative_review", "scoping_review", "editorial", "commentary", "letter",
    "program_description", "qi_report", "survey", "economic_evaluation", "expert_opinion",
)

# Maps each canonical ID to its NHMRC level. Consumed by evidence_classifier
# for fast direct lookup before falling back to legacy substring matching.
CANONICAL_TO_NHMRC: dict[str, EvidenceLevel] = {
    "systematic_review": EvidenceLevel.level_I,
    "meta_analysis": EvidenceLevel.level_I,
    "umbrella_review": EvidenceLevel.level_I,
    "rct": EvidenceLevel.level_II,
    "cluster_rct": EvidenceLevel.level_II,
    "quasi_rct": EvidenceLevel.level_III_1,
    "non_randomized_trial": EvidenceLevel.level_III_1,
    "controlled_before_after": EvidenceLevel.level_III_1,
    "interrupted_time_series": EvidenceLevel.level_III_1,
    "prospective_cohort": EvidenceLevel.level_III_2,
    "comparative_study": EvidenceLevel.level_III_2,
    "retrospective_cohort": EvidenceLevel.level_III_3,
    "case_control": EvidenceLevel.level_III_3,
    "historical_control": EvidenceLevel.level_III_3,
    "case_series": EvidenceLevel.level_IV,
    "case_report": EvidenceLevel.level_IV,
    "cross_sectional": EvidenceLevel.level_IV,
    "qualitative": EvidenceLevel.level_IV,
    "mixed_methods": EvidenceLevel.level_IV,
    "narrative_review": EvidenceLevel.level_IV,
    "scoping_review": EvidenceLevel.level_IV,
    "editorial": EvidenceLevel.level_IV,
    "commentary": EvidenceLevel.level_IV,
    "letter": EvidenceLevel.level_IV,
    "program_description": EvidenceLevel.level_IV,
    "qi_report": EvidenceLevel.level_IV,
    "survey": EvidenceLevel.level_IV,
    "economic_evaluation": EvidenceLevel.level_IV,
    "expert_opinion": EvidenceLevel.level_IV,
}

# Maps observed free-text labels to canonical IDs. Slash-disjunction keys
# (e.g. "editorial/commentary") capture sub-agents hedging between two terms;
# map both to the more-specific one.
_LEGACY_LABEL_MAP: dict[str, str] = {
    # Level I
    "systematic review with meta-analysis": "meta_analysis",
    "systematic review with meta analysis": "meta_analysis",
    "network meta-analysis": "meta_analysis",
    "network meta analysis": "meta_analysis",
    "systematic literature review": "systematic_review",
    "systematic review": "systematic_review",
    "meta-analysis": "meta_analysis",
    "meta analysis": "meta_analysis",
    "umbrella review": "umbrella_review",
    # Level II
    "cluster randomized controlled trial": "cluster_rct",
    "cluster randomised controlled trial": "cluster_rct",
    "cluster randomized trial": "cluster_rct",
    "cluster randomised trial": "cluster_rct",
    "randomized controlled trial": "rct",
    "randomised controlled trial": "rct",
    "randomized trial": "rct",
    "randomised trial": "rct",
    "rct": "rct",
    # Level III-1
    "interrupted time series": "interrupted_time_series",
    "controlled before-after": "controlled_before_after",
    "controlled before after": "controlled_before_after",
    "before-after study with control": "controlled_before_after",
    "pseudo-randomized": "quasi_rct",
    "pseudo randomized": "quasi_rct",
    "quasi-randomized": "quasi_rct",
    "quasi randomized": "quasi_rct",
    "quasi-experimental": "quasi_rct",
    "non-randomized trial": "non_randomized_trial",
    "non-randomised trial": "non_randomized_trial",
    "non randomized trial": "non_randomized_trial",
    # Level III-2
    "prospective comparative cohort": "prospective_cohort",
    "prospective cohort study": "prospective_cohort",
    "prospective cohort": "prospective_cohort",
    "prospective observational study": "prospective_cohort",
    "prospective study": "prospective_cohort",
    "comparative study": "comparative_study",
    "controlled study": "comparative_study",
    # Level III-3
    "nested case-control": "case_control",
    "nested case control": "case_control",
    "retrospective comparative cohort": "retrospective_cohort",
    "retrospective pre-post cohort": "retrospective_cohort",
    "retrospective cohort study": "retrospective_cohort",
    "retrospective cohort": "retrospective_cohort",
    "cohort study": "retrospective_cohort",
    "case-control": "case_control",
    "case control": "case_control",
    "historical control": "historical_control",
    # Level IV
    "qualitative interview study": "qualitative",
    "qualitative study": "qualitative",
    "mixed-methods survey": "mixed_methods",
    "mixed methods survey": "mixed_methods",
    "mixed-methods": "mixed_methods",
    "mixed methods": "mixed_methods",
    "cross-sectional survey": "cross_sectional",
    "cross sectional survey": "cross_sectional",
    "cross-sectional": "cross_sectional",
    "cross sectional": "cross_sectional",
    "descriptive study": "case_series",
    "case series": "case_series",
    "case report": "case_report",
    "narrative review/book chapter": "narrative_review",
    "technical report/narrative review": "narrative_review",
    "narrative review": "narrative_review",
    "scoping review": "scoping_review",
    "editorial/commentary": "editorial",
    "editorial": "editorial",
    "commentary": "commentary",
    "letter to the editor": "letter",
    "letter": "letter",
    "program description / case series": "program_description",
    "program description": "program_description",
    "quality improvement report": "qi_report",
    "qi report": "qi_report",
    "qi project": "qi_report",
    "survey": "survey",
    "economic evaluation": "economic_evaluation",
    "cost-effectiveness analysis": "economic_evaluation",
    "expert opinion": "expert_opinion",
    "consensus": "expert_opinion",
}

# Substring-fallback iteration order: longest keys first so "retrospective cohort
# study" wins over "cohort study" when scanning a label like "single-center
# retrospective cohort study". Sorted at import time so the invariant holds
# regardless of dict declaration order.
_LEGACY_LABEL_PAIRS_BY_LENGTH: tuple[tuple[str, str], ...] = tuple(
    sorted(_LEGACY_LABEL_MAP.items(), key=lambda kv: -len(kv[0]))
)


def canonicalize_study_design(raw: str | None) -> str | None:
    """Normalize a free-text study_design label to a canonical ID.

    Strategy: (1) already-canonical → passthrough; (2) lookup in legacy map;
    (3) substring scan of legacy keys (handles "single-center retrospective
    cohort study" → "retrospective_cohort"). Returns None if no match — caller
    should keep the raw value so evidence_classifier's substring fallback can
    still attempt classification.
    """
    if not raw:
        return None
    s = raw.strip().lower()
    if not s:
        return None
    if s in CANONICAL_TO_NHMRC:
        return s
    if s in _LEGACY_LABEL_MAP:
        return _LEGACY_LABEL_MAP[s]
    for legacy_key, canonical in _LEGACY_LABEL_PAIRS_BY_LENGTH:
        if legacy_key in s:
            return canonical
    return None


def assign_study_design(article: Article, raw: str) -> None:
    """Set article.study_design from `raw`, normalizing via the controlled vocabulary.

    Falls back to the raw value when no canonical mapping exists so
    evidence_classifier's substring scan still has something to match.
    """
    article.study_design = canonicalize_study_design(raw) or raw
