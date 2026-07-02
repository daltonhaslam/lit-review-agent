"""Study-design canonical vocabulary, backfill mode, and Stage 11 coverage warnings."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent import state as state_manager
from src.models.article import Article, EvidenceLevel, ExtractionSource
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills import _study_design as sd_module
from src.skills import data_extractor as de_module
from src.skills._study_design import (
    CANONICAL_STUDY_DESIGNS,
    CANONICAL_TO_NHMRC,
    canonicalize_study_design,
)
from src.skills.data_extractor import (
    _build_coverage_and_warnings,
    canonicalize_existing,
)
from src.skills.evidence_classifier import classify_level


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_article(**kwargs) -> Article:
    defaults = dict(title="Sample article", year=2024, authors=["Smith J"])
    defaults.update(kwargs)
    return Article(**defaults)


def _make_review(tmp_path: Path) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-review"
    review_dir.mkdir()
    pico = PICO(P="P", I="I", C="C", O=["O"], inclusion_criteria=["inc"], exclusion_criteria=["exc"])
    review = Review(
        review_id="test-review",
        raw_question="q",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        pico=pico,
        queries=QuerySet(pubmed=[Query(step_number="#1", query_string="x", database="pubmed")]),
        status=ReviewStatus.fulltext_assessed,
    )
    state_manager.save(review, review_dir)
    return review, review_dir


# ---------------------------------------------------------------------------
# canonicalize_study_design — preference ladder
# ---------------------------------------------------------------------------

class TestCanonicalize:
    @pytest.mark.parametrize("raw,expected", [
        # Already canonical → passthrough
        ("rct", "rct"),
        ("retrospective_cohort", "retrospective_cohort"),
        # Direct legacy hits
        ("RCT", "rct"),
        ("randomized controlled trial", "rct"),
        ("randomised controlled trial", "rct"),
        ("Cluster Randomized Trial", "cluster_rct"),
        ("retrospective cohort", "retrospective_cohort"),
        ("retrospective cohort study", "retrospective_cohort"),
        ("retrospective comparative cohort", "retrospective_cohort"),
        ("cohort study", "retrospective_cohort"),
        ("case-control", "case_control"),
        ("case control", "case_control"),
        ("prospective cohort", "prospective_cohort"),
        ("prospective observational study", "prospective_cohort"),
        # Level I — most-specific-first ordering matters
        ("systematic review with meta-analysis", "meta_analysis"),
        ("network meta-analysis", "meta_analysis"),
        ("systematic review", "systematic_review"),
        ("meta-analysis", "meta_analysis"),
        # Level IV — fragmented variants seen in real reviews
        ("narrative review", "narrative_review"),
        ("narrative review/book chapter", "narrative_review"),
        ("technical report/narrative review", "narrative_review"),
        ("qualitative study", "qualitative"),
        ("qualitative interview study", "qualitative"),
        ("editorial/commentary", "editorial"),
        ("cross-sectional survey", "cross_sectional"),
        # Substring fallback
        ("single-center retrospective cohort study", "retrospective_cohort"),
        # No mapping
        ("something completely unknown", None),
        # Empty inputs
        (None, None),
        ("", None),
        ("   ", None),
    ])
    def test_known_labels(self, raw, expected):
        assert canonicalize_study_design(raw) == expected


# ---------------------------------------------------------------------------
# Vocabulary integrity — every canonical ID must map to an NHMRC level
# ---------------------------------------------------------------------------

class TestVocabularyIntegrity:
    def test_every_canonical_has_nhmrc_mapping(self):
        missing = [d for d in CANONICAL_STUDY_DESIGNS if d not in CANONICAL_TO_NHMRC]
        assert missing == [], f"canonical IDs missing NHMRC mapping: {missing}"

    def test_every_nhmrc_mapping_targets_canonical_id(self):
        rogue = [k for k in CANONICAL_TO_NHMRC if k not in CANONICAL_STUDY_DESIGNS]
        assert rogue == [], f"NHMRC mapping has non-canonical keys: {rogue}"

    def test_every_legacy_value_targets_canonical_id(self):
        bad = [(k, v) for k, v in sd_module._LEGACY_LABEL_MAP.items() if v not in CANONICAL_STUDY_DESIGNS]
        assert bad == [], f"legacy map points to non-canonical IDs: {bad}"

    def test_legacy_pairs_sorted_by_descending_key_length(self):
        # Ordering is load-bearing for the substring fallback.
        lengths = [len(k) for k, _ in sd_module._LEGACY_LABEL_PAIRS_BY_LENGTH]
        assert lengths == sorted(lengths, reverse=True)


# ---------------------------------------------------------------------------
# evidence_classifier — canonical IDs route to expected NHMRC levels
# ---------------------------------------------------------------------------

class TestClassifierWithCanonicalIds:
    @pytest.mark.parametrize("canonical,expected", [
        ("systematic_review", EvidenceLevel.level_I),
        ("meta_analysis", EvidenceLevel.level_I),
        ("rct", EvidenceLevel.level_II),
        ("cluster_rct", EvidenceLevel.level_II),
        ("quasi_rct", EvidenceLevel.level_III_1),
        ("interrupted_time_series", EvidenceLevel.level_III_1),
        ("prospective_cohort", EvidenceLevel.level_III_2),
        ("retrospective_cohort", EvidenceLevel.level_III_3),
        ("case_control", EvidenceLevel.level_III_3),
        ("case_series", EvidenceLevel.level_IV),
        ("narrative_review", EvidenceLevel.level_IV),
        ("scoping_review", EvidenceLevel.level_IV),
        ("editorial", EvidenceLevel.level_IV),
        ("expert_opinion", EvidenceLevel.level_IV),
    ])
    def test_canonical_routes(self, canonical, expected):
        assert classify_level(canonical) == expected

    def test_legacy_freetext_still_routes(self):
        # Backwards compat: existing reviews carry free-text labels; substring fallback still works.
        assert classify_level("retrospective cohort study") == EvidenceLevel.level_III_3
        assert classify_level("randomized controlled trial") == EvidenceLevel.level_II

    def test_unknown_returns_unknown(self):
        assert classify_level("something nobody has ever published") == EvidenceLevel.unknown


# ---------------------------------------------------------------------------
# _apply_extraction normalizes incoming labels
# ---------------------------------------------------------------------------

class TestApplyExtractionCanonicalizes:
    def test_canonicalizes_incoming_label(self):
        a = _make_article()
        de_module._apply_extraction(a, {"study_design": "Retrospective Cohort Study"})
        assert a.study_design == "retrospective_cohort"

    def test_already_canonical_unchanged(self):
        a = _make_article()
        de_module._apply_extraction(a, {"study_design": "rct"})
        assert a.study_design == "rct"

    def test_unknown_label_passes_through(self):
        # Preserves raw value so evidence_classifier can attempt substring matching.
        a = _make_article()
        de_module._apply_extraction(a, {"study_design": "novel synthesis methodology"})
        assert a.study_design == "novel synthesis methodology"


# ---------------------------------------------------------------------------
# canonicalize_existing — backfill mode
# ---------------------------------------------------------------------------

class TestCanonicalizeExisting:
    def test_rewrites_legacy_labels_in_place(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        articles = [
            _make_article(study_design="Retrospective Cohort Study").model_dump(mode="json"),
            _make_article(study_design="narrative review/book chapter").model_dump(mode="json"),
            _make_article(study_design="rct").model_dump(mode="json"),  # already canonical
            _make_article(study_design=None).model_dump(mode="json"),
            _make_article(study_design="totally unknown design").model_dump(mode="json"),
        ]
        (review_dir / review.articles_included_file).write_text(json.dumps(articles, indent=2))

        canonicalize_existing(review_dir)

        rows = json.loads((review_dir / review.articles_included_file).read_text())
        assert rows[0]["study_design"] == "retrospective_cohort"
        assert rows[1]["study_design"] == "narrative_review"
        assert rows[2]["study_design"] == "rct"
        assert rows[3]["study_design"] is None
        # Unmapped values left intact for the classifier substring fallback.
        assert rows[4]["study_design"] == "totally unknown design"

    def test_idempotent(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        articles = [_make_article(study_design="cohort study").model_dump(mode="json")]
        (review_dir / review.articles_included_file).write_text(json.dumps(articles, indent=2))

        canonicalize_existing(review_dir)
        first = json.loads((review_dir / review.articles_included_file).read_text())
        canonicalize_existing(review_dir)
        second = json.loads((review_dir / review.articles_included_file).read_text())

        assert first == second
        assert first[0]["study_design"] == "retrospective_cohort"

    def test_writes_evidence_table_when_changes_made(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        # Use a non-canonical label so changed > 0 and the file write fires.
        articles = [_make_article(study_design="Retrospective Cohort Study").model_dump(mode="json")]
        (review_dir / review.articles_included_file).write_text(json.dumps(articles, indent=2))

        canonicalize_existing(review_dir)

        assert (review_dir / "evidence_table.csv").exists()

    def test_skips_writes_when_nothing_changed(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        # Already-canonical input: no rewrite, no CSV churn.
        articles = [_make_article(study_design="rct").model_dump(mode="json")]
        (review_dir / review.articles_included_file).write_text(json.dumps(articles, indent=2))

        canonicalize_existing(review_dir)

        assert not (review_dir / "evidence_table.csv").exists()


# ---------------------------------------------------------------------------
# _build_coverage_and_warnings — per-field thresholds
# ---------------------------------------------------------------------------

class TestCoverageStats:
    def test_extraction_coverage_has_new_fields(self):
        articles = [_make_article(
            study_design="rct",
            primary_outcomes=["x"],
            effect_sizes="OR 1.2",
            author_conclusions="works",
            funding_source="NIH",
            coi_declared=False,
            intervention_details="thing",
            comparison_details="other thing",
            extraction_source=ExtractionSource.full_text,
        )]
        coverage, _ = _build_coverage_and_warnings(articles)
        for key in ("with_funding", "with_coi", "with_conclusions",
                    "with_intervention", "with_comparison", "articles_with_empty_input"):
            assert key in coverage, f"missing {key} in coverage dict"
        assert coverage["with_funding"] == 1
        assert coverage["with_coi"] == 1  # False is a present declaration
        assert coverage["with_conclusions"] == 1
        assert coverage["with_intervention"] == 1
        assert coverage["with_comparison"] == 1
        assert coverage["articles_with_empty_input"] == 0


class TestWarnings:
    def test_low_conclusions_triggers_warning(self):
        articles = [
            _make_article(extraction_source=ExtractionSource.full_text, author_conclusions=None)
            for _ in range(10)
        ]
        articles[0].author_conclusions = "summary"  # 1/10 = 10% < 80%
        _, warnings = _build_coverage_and_warnings(articles)
        assert any("author_conclusions" in w for w in warnings)

    def test_high_conclusions_no_warning(self):
        articles = [
            _make_article(extraction_source=ExtractionSource.full_text, author_conclusions="summary")
            for _ in range(10)
        ]
        _, warnings = _build_coverage_and_warnings(articles)
        assert not any("author_conclusions" in w for w in warnings)

    def test_low_funding_with_full_text_triggers_warning(self):
        # 0/10 funding, all full_text — should warn
        articles = [
            _make_article(extraction_source=ExtractionSource.full_text, author_conclusions="x")
            for _ in range(10)
        ]
        _, warnings = _build_coverage_and_warnings(articles)
        assert any("funding_source" in w for w in warnings)

    def test_low_funding_without_full_text_no_warning(self):
        # 0/10 funding, NO full_text — abstract-only is expected to lack funding, don't warn
        articles = [
            _make_article(extraction_source=ExtractionSource.abstract, abstract="x", author_conclusions="x")
            for _ in range(10)
        ]
        _, warnings = _build_coverage_and_warnings(articles)
        assert not any("funding_source" in w for w in warnings)

    def test_empty_input_articles_flagged(self):
        articles = [
            _make_article(extraction_source=ExtractionSource.none, author_conclusions="x")
            for _ in range(3)
        ]
        # Pad with full-text articles so the conclusions/funding warnings don't dominate
        articles += [
            _make_article(
                extraction_source=ExtractionSource.full_text,
                author_conclusions="x",
                funding_source="NIH",
            )
            for _ in range(7)
        ]
        _, warnings = _build_coverage_and_warnings(articles)
        empty_warning = next((w for w in warnings if "empty input" in w), None)
        assert empty_warning is not None
        assert "3" in empty_warning

    def test_no_warnings_when_coverage_is_clean(self):
        articles = [
            _make_article(
                extraction_source=ExtractionSource.full_text,
                author_conclusions="summary",
                funding_source="NIH",
            )
            for _ in range(10)
        ]
        _, warnings = _build_coverage_and_warnings(articles)
        assert warnings == []

    def test_empty_articles_list_no_warnings(self):
        coverage, warnings = _build_coverage_and_warnings([])
        assert warnings == []
        assert coverage["total"] == 0
