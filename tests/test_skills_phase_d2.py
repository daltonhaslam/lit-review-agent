"""Unit tests for Phase D2 — accuracy safeguards.

Tests:
  TestEvidenceMapFix (5)         — _build_evidence_map counts by evidence_level (not fuzzy match)
  TestSurrogateOutcomeNull (3)   — _is_surrogate_outcome returns None for empty inputs
  TestDataExtractorCoverage (4)  — extraction_coverage emitted in finalization output

No LLM or API calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import state as state_manager
from src.models.article import Article, EvidenceLevel, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.evidence_classifier import _is_surrogate_outcome
from src.skills.synthesizer import _build_evidence_map
from src.skills.data_extractor import save as de_save


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Adult inpatients with heart failure",
        I="Care transitions",
        C="Usual care",
        O=["30-day all-cause readmission", "all-cause mortality"],
        inclusion_criteria=["Adults ≥18"],
        exclusion_criteria=["Pediatric"],
    )
    defaults.update(kwargs)
    return PICO(**defaults)


def _make_review(tmp_path: Path, status=ReviewStatus.fulltext_assessed, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "hf-2026"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="hf-2026",
        raw_question="Do care transitions reduce HF readmission?",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        status=status,
        pico=_make_pico(),
        queries=QuerySet(
            pubmed=[Query(step_number="#1", query_string='"Heart Failure"[Mesh]', database="pubmed")],
        ),
    )
    defaults.update(kwargs)
    review = Review(**defaults)
    state_manager.save(review, review_dir)
    return review, review_dir


def _make_article(**kwargs) -> Article:
    defaults = dict(
        title="A CHF study",
        authors=["Smith J"],
        year=2022,
        journal="NEJM",
        source_dbs=["pubmed"],
        screening_decision=ScreeningDecision.include,
    )
    defaults.update(kwargs)
    return Article(**defaults)


def _write_included(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_included.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _minimal_extractions(articles: list[Article], offset: int = 0) -> str:
    return json.dumps({"extractions": [
        {"orig_idx": offset + i, "study_design": a.study_design}
        for i, a in enumerate(articles)
    ]})


# ===========================================================================
# TestEvidenceMapFix
# ===========================================================================

class TestEvidenceMapFix:
    def test_returns_one_row_per_outcome(self):
        articles = [_make_article(evidence_level=EvidenceLevel.level_II)]
        rows = _build_evidence_map(articles, ["readmission", "mortality"])
        assert len(rows) == 2

    def test_total_equals_all_included_articles(self):
        articles = [
            _make_article(evidence_level=EvidenceLevel.level_I),
            _make_article(evidence_level=EvidenceLevel.level_II),
            _make_article(evidence_level=EvidenceLevel.level_II),
        ]
        rows = _build_evidence_map(articles, ["readmission"])
        assert rows[0]["total"] == 3

    def test_by_design_counts_match_level_distribution(self):
        articles = [
            _make_article(evidence_level=EvidenceLevel.level_I),
            _make_article(evidence_level=EvidenceLevel.level_II),
            _make_article(evidence_level=EvidenceLevel.level_II),
        ]
        rows = _build_evidence_map(articles, ["readmission"])
        assert rows[0]["by_design"]["SR/MA"] == 1
        assert rows[0]["by_design"]["RCT"] == 2

    def test_all_outcome_rows_have_same_counts(self):
        articles = [
            _make_article(evidence_level=EvidenceLevel.level_II, primary_outcomes=[]),
            _make_article(evidence_level=EvidenceLevel.level_IV, primary_outcomes=[]),
        ]
        rows = _build_evidence_map(articles, ["readmission", "mortality"])
        assert rows[0]["total"] == rows[1]["total"] == 2

    def test_empty_articles_yields_zero_totals(self):
        rows = _build_evidence_map([], ["readmission"])
        assert rows[0]["total"] == 0
        assert rows[0]["by_design"] == {}


# ===========================================================================
# TestSurrogateOutcomeNull
# ===========================================================================

class TestSurrogateOutcomeNull:
    def test_returns_none_when_article_outcomes_empty(self):
        result = _is_surrogate_outcome([], ["30-day readmission"])
        assert result is None

    def test_returns_none_when_pico_outcomes_empty(self):
        result = _is_surrogate_outcome(["30-day readmission"], [])
        assert result is None

    def test_returns_false_when_outcomes_overlap(self):
        result = _is_surrogate_outcome(["30-day readmission"], ["30-day readmission"])
        assert result is False

    def test_returns_true_when_no_overlap(self):
        result = _is_surrogate_outcome(["troponin level"], ["30-day readmission", "mortality"])
        assert result is True


# ===========================================================================
# TestDataExtractorCoverage
# ===========================================================================

def _capture_de_save(capsys, review_dir, extractions_json, batch_size, offset) -> dict:
    de_save(review_dir, extractions_json, batch_size=batch_size, batch_offset=offset)
    return json.loads(capsys.readouterr().out)


class TestDataExtractorCoverage:
    def test_coverage_present_on_completion(self, tmp_path, capsys):
        articles = [_make_article(study_design="RCT")]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, articles)
        out = _capture_de_save(capsys, review_dir, _minimal_extractions(articles), len(articles), 0)
        assert "extraction_coverage" in out

    def test_coverage_total_matches_article_count(self, tmp_path, capsys):
        articles = [_make_article(pmid=str(i)) for i in range(5)]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, articles)
        out = _capture_de_save(capsys, review_dir, _minimal_extractions(articles), len(articles), 0)
        assert out["extraction_coverage"]["total"] == 5

    def test_coverage_effect_size_pct_computed(self, tmp_path, capsys):
        articles = [
            _make_article(pmid="1", effect_sizes="OR 0.74"),
            _make_article(pmid="2"),
            _make_article(pmid="3"),
            _make_article(pmid="4"),
        ]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, articles)
        out = _capture_de_save(capsys, review_dir, _minimal_extractions(articles), len(articles), 0)
        assert out["extraction_coverage"]["with_effect_sizes"] == 1
        assert abs(out["extraction_coverage"]["effect_size_pct"] - 0.25) < 0.001

    def test_coverage_absent_on_partial_batch(self, tmp_path, capsys):
        articles = [_make_article(pmid=str(i)) for i in range(5)]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, articles)
        out = _capture_de_save(capsys, review_dir, _minimal_extractions(articles[:3]), 3, 0)
        assert "extraction_coverage" not in out
        assert out["complete"] is False
