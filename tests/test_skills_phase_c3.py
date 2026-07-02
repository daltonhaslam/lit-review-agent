"""Unit tests for Phase C3 — fulltext_assessor and data_extractor subscription conversion.

Tests the --prepare / --save CLI pattern for both skills. No LLM or API calls.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.fulltext_assessor import prepare as ft_prepare, save as ft_save
from src.skills.data_extractor import prepare as de_prepare, save as de_save


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Adult patients hospitalized with heart failure",
        I="Care transitions interventions",
        C="Usual care",
        O=["30-day all-cause readmission rate", "all-cause mortality"],
        inclusion_criteria=["Adults ≥18", "Primary diagnosis of heart failure"],
        exclusion_criteria=["Pediatric population", "Non-English language"],
    )
    defaults.update(kwargs)
    return PICO(**defaults)


def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "chf-2026-04"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="chf-2026-04",
        raw_question="Do care transitions reduce CHF readmission?",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        status=ReviewStatus.retrieval_list_generated,
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
        title="A study on heart failure readmission",
        authors=["Smith J", "Jones A"],
        year=2022,
        journal="NEJM",
        source_dbs=["pubmed"],
        screening_decision=ScreeningDecision.include,
    )
    defaults.update(kwargs)
    return Article(**defaults)


def _write_screened(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_screened.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _write_included(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_included.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _capture_ft_prepare(capsys, review_dir, pdf_dir=None) -> dict:
    ft_prepare(review_dir, pdf_dir)
    return json.loads(capsys.readouterr().out)


def _capture_ft_save(capsys, review_dir, decisions: list[dict]) -> dict:
    ft_save(review_dir, json.dumps({"decisions": decisions}))
    return json.loads(capsys.readouterr().out)


def _capture_de_prepare(capsys, review_dir) -> dict:
    de_prepare(review_dir)
    return json.loads(capsys.readouterr().out)


def _capture_de_save(capsys, review_dir, extractions: list[dict]) -> dict:
    de_save(review_dir, json.dumps({"extractions": extractions}))
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# fulltext_assessor — prepare
# ---------------------------------------------------------------------------

class TestFulltextAssessorPrepare:
    def test_wrong_status_dies(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [_make_article()])
        with pytest.raises(SystemExit):
            ft_prepare(review_dir)
        assert "error" in json.loads(capsys.readouterr().out)

    def test_no_pico_dies(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=None)
        _write_screened(review_dir, [_make_article()])
        with pytest.raises(SystemExit):
            ft_prepare(review_dir)
        assert "error" in json.loads(capsys.readouterr().out)

    def test_includes_eligible_articles(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, [
            _make_article(pmid="1", screening_decision=ScreeningDecision.include),
            _make_article(pmid="2", screening_decision=ScreeningDecision.borderline),
        ])
        out = _capture_ft_prepare(capsys, review_dir)
        assert out["total_eligible"] == 2
        assert len(out["articles"]) == 2

    def test_excludes_non_eligible_articles(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, [
            _make_article(pmid="1", screening_decision=ScreeningDecision.include),
            _make_article(pmid="2", screening_decision=ScreeningDecision.exclude),
        ])
        out = _capture_ft_prepare(capsys, review_dir)
        assert out["total_eligible"] == 1
        assert out["articles"][0]["orig_idx"] == 0

    def test_article_has_orig_idx(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, [
            _make_article(pmid="1", screening_decision=ScreeningDecision.exclude),
            _make_article(pmid="2", screening_decision=ScreeningDecision.include),
        ])
        out = _capture_ft_prepare(capsys, review_dir)
        assert out["articles"][0]["orig_idx"] == 1  # second article in screened list

    def test_no_pdf_gives_null_fulltext(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, [_make_article(pmid="1")])
        out = _capture_ft_prepare(capsys, review_dir, pdf_dir=tmp_path / "empty_pdfs")
        assert out["articles"][0]["fulltext"] is None
        assert out["articles"][0]["pdf_found"] is False

    def test_pdf_matched_extracts_fulltext(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        (pdf_dir / "1.pdf").write_bytes(b"%PDF")
        _write_screened(review_dir, [_make_article(pmid="1")])
        with patch("src.skills.fulltext_assessor._extract_text_and_full",
                   return_value=("Extracted text", "Extracted text")):
            out = _capture_ft_prepare(capsys, review_dir, pdf_dir=pdf_dir)
        assert out["articles"][0]["pdf_found"] is True
        assert out["articles"][0]["fulltext"] == "Extracted text"
        assert out["pdfs_matched"] == 1

    def test_output_includes_assessment_task(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, [_make_article()])
        out = _capture_ft_prepare(capsys, review_dir)
        assert "assessment_task" in out
        assert "orig_idx" in out["assessment_task"]

    def test_output_includes_pico_context(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, [_make_article()])
        out = _capture_ft_prepare(capsys, review_dir)
        assert "pico_context" in out
        assert "P (Population):" in out["pico_context"]

    def test_empty_eligible_set_outputs_zero_articles(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, [_make_article(screening_decision=ScreeningDecision.exclude)])
        out = _capture_ft_prepare(capsys, review_dir)
        assert out["total_eligible"] == 0
        assert out["articles"] == []


# ---------------------------------------------------------------------------
# fulltext_assessor — save
# ---------------------------------------------------------------------------

class TestFulltextAssessorSave:
    def _setup(self, tmp_path, articles=None):
        _, review_dir = _make_review(tmp_path)
        if articles is None:
            articles = [_make_article(pmid="1")]
        _write_screened(review_dir, articles)
        return review_dir

    def test_include_decision_written_to_included_file(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1")])
        _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "include", "rationale": "Meets all criteria", "study_design": "RCT"}
        ])
        included = json.loads((review_dir / "articles_included.json").read_text())
        assert len(included) == 1

    def test_exclude_decision_not_in_included_file(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1")])
        _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "exclude", "rationale": "Pediatric only", "study_design": "Cohort"}
        ])
        included = json.loads((review_dir / "articles_included.json").read_text())
        assert len(included) == 0

    def test_missing_decision_marks_pending(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1")])
        out = _capture_ft_save(capsys, review_dir, [])  # no decisions provided
        assert out["pending_retrieval"] == 1
        assert out["assessed"] == 0

    def test_save_preserves_sought_count_and_tracks_not_retrieved(self, tmp_path, capsys):
        # 3 eligible; only 2 get decisions (the 3rd has no PDF → pending). "Reports sought for
        # retrieval" (eligibility) must stay 3, not collapse to the 2 assessed — else the PRISMA
        # chain silently drops the not-retrieved article.
        review_dir = self._setup(tmp_path, [
            _make_article(pmid="1"), _make_article(pmid="2"), _make_article(pmid="3"),
        ])
        _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "include", "rationale": "OK", "study_design": "RCT"},
            {"orig_idx": 1, "decision": "exclude", "rationale": "Wrong pop", "study_design": "Cohort"},
        ])
        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.eligibility == 3        # reports sought for retrieval
        assert saved.prisma_counts.fulltext_assessed == 2  # actually assessed (had a PDF)
        assert saved.prisma_counts.not_retrieved == 1      # no PDF available
        assert saved.prisma_counts.included == 1

    def test_prisma_eligibility_count_set(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [
            _make_article(pmid="1"),
            _make_article(pmid="2"),
        ])
        _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "include", "rationale": "OK", "study_design": "RCT"},
            {"orig_idx": 1, "decision": "exclude", "rationale": "Wrong population", "study_design": "Cohort"},
        ])
        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.eligibility == 2

    def test_prisma_included_count_set(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [
            _make_article(pmid="1"),
            _make_article(pmid="2"),
        ])
        _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "include", "rationale": "OK", "study_design": "RCT"},
            {"orig_idx": 1, "decision": "exclude", "rationale": "Wrong population", "study_design": "Cohort"},
        ])
        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.included == 1

    def test_status_updated_to_fulltext_assessed(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1")])
        _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "include", "rationale": "OK", "study_design": "RCT"}
        ])
        saved = state_manager.load(review_dir)
        assert saved.status == ReviewStatus.fulltext_assessed

    def test_study_design_stored_on_included_article(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1")])
        _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "include", "rationale": "OK", "study_design": "RCT"}
        ])
        included = json.loads((review_dir / "articles_included.json").read_text())
        assert included[0]["study_design"] == "rct"

    def test_excluded_count_in_output(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [
            _make_article(pmid="1"),
            _make_article(pmid="2"),
        ])
        out = _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "include", "rationale": "OK", "study_design": "RCT"},
            {"orig_idx": 1, "decision": "exclude", "rationale": "Wrong outcome", "study_design": "Cohort"},
        ])
        assert out["excluded_fulltext"] == 1
        assert out["included"] == 1

    def test_borderline_screened_articles_eligible_for_save(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, [
            _make_article(pmid="1", screening_decision=ScreeningDecision.borderline),
        ])
        out = _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "include", "rationale": "Confirmed eligible", "study_design": "SR"}
        ])
        assert out["included"] == 1

    def test_no_eligible_articles_produces_empty_included_file(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, [_make_article(screening_decision=ScreeningDecision.exclude)])
        _capture_ft_save(capsys, review_dir, [])
        included = json.loads((review_dir / "articles_included.json").read_text())
        assert included == []


# ---------------------------------------------------------------------------
# fulltext_assessor — excluded_fulltext write-back (#20)
# ---------------------------------------------------------------------------

class TestFulltextAssessorExcludedFulltext:
    def test_excluded_fulltext_written_to_state(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, [
            _make_article(pmid="1"),
            _make_article(pmid="2"),
        ])
        _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "include", "rationale": "Meets criteria", "study_design": "RCT"},
            {"orig_idx": 1, "decision": "exclude", "rationale": "Wrong population", "study_design": "Cohort"},
        ])
        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.excluded_fulltext != {}
        total_excluded = sum(saved.prisma_counts.excluded_fulltext.values())
        assert total_excluded == 1

    def test_excluded_fulltext_reason_key_is_rationale(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, [_make_article(pmid="1")])
        _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "exclude", "rationale": "Pediatric population only", "study_design": "Cohort"},
        ])
        saved = state_manager.load(review_dir)
        assert any("Pediatric" in k for k in saved.prisma_counts.excluded_fulltext)

    def test_no_excluded_leaves_empty_dict(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, [_make_article(pmid="1")])
        _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "include", "rationale": "Meets all criteria", "study_design": "RCT"},
        ])
        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.excluded_fulltext == {}

    def test_multiple_exclusion_reasons_tallied(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(pmid=str(i)) for i in range(4)]
        _write_screened(review_dir, articles)
        _capture_ft_save(capsys, review_dir, [
            {"orig_idx": 0, "decision": "exclude", "rationale": "Wrong population", "study_design": "Cohort"},
            {"orig_idx": 1, "decision": "exclude", "rationale": "Wrong population", "study_design": "Cohort"},
            {"orig_idx": 2, "decision": "exclude", "rationale": "No full text", "study_design": "RCT"},
            {"orig_idx": 3, "decision": "include", "rationale": "Meets all", "study_design": "RCT"},
        ])
        saved = state_manager.load(review_dir)
        total = sum(saved.prisma_counts.excluded_fulltext.values())
        assert total == 3




# ---------------------------------------------------------------------------
# data_extractor — prepare
# ---------------------------------------------------------------------------

class TestDataExtractorPrepare:
    def _setup(self, tmp_path, articles=None):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.fulltext_assessed)
        if articles is None:
            articles = [_make_article()]
        _write_included(review_dir, articles)
        return review_dir

    def test_wrong_status_dies(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article()])
        with pytest.raises(SystemExit):
            de_prepare(review_dir)
        assert "error" in json.loads(capsys.readouterr().out)

    def test_outputs_all_included_articles(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid=str(i)) for i in range(3)])
        out = _capture_de_prepare(capsys, review_dir)
        assert out["total_articles"] == 3
        assert len(out["articles"]) == 3

    def test_article_has_orig_idx(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1"), _make_article(pmid="2")])
        out = _capture_de_prepare(capsys, review_dir)
        assert out["articles"][0]["orig_idx"] == 0
        assert out["articles"][1]["orig_idx"] == 1

    def test_article_text_includes_title(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(title="Landmark CHF study")])
        out = _capture_de_prepare(capsys, review_dir)
        assert "Landmark CHF study" in out["articles"][0]["article_text"]

    def test_extraction_task_in_output(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        out = _capture_de_prepare(capsys, review_dir)
        assert "extraction_task" in out
        assert "orig_idx" in out["extraction_task"]

    def test_empty_included_set_outputs_zero(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [])
        out = _capture_de_prepare(capsys, review_dir)
        assert out["total_articles"] == 0
        assert out["articles"] == []


# ---------------------------------------------------------------------------
# data_extractor — save
# ---------------------------------------------------------------------------

class TestDataExtractorSave:
    def _setup(self, tmp_path, articles=None):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.fulltext_assessed)
        if articles is None:
            articles = [_make_article()]
        _write_included(review_dir, articles)
        return review_dir

    def test_applies_extraction_fields(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1")])
        _capture_de_save(capsys, review_dir, [
            {"orig_idx": 0, "study_design": "RCT", "sample_size": 300,
             "follow_up_duration": "6 months", "intervention_details": "Nurse transitions",
             "comparison_details": "Usual care", "primary_outcomes": ["30-day readmission"],
             "effect_sizes": "OR 0.65", "secondary_outcomes": ["Mortality"],
             "author_conclusions": "Effective", "funding_source": "NIH", "coi_declared": False}
        ])
        articles = json.loads((review_dir / "articles_included.json").read_text())
        assert articles[0]["study_design"] == "rct"
        assert articles[0]["sample_size"] == 300
        assert articles[0]["primary_outcomes"] == ["30-day readmission"]

    def test_writes_evidence_table_csv(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1")])
        _capture_de_save(capsys, review_dir, [
            {"orig_idx": 0, "study_design": "RCT", "sample_size": None,
             "follow_up_duration": None, "intervention_details": None,
             "comparison_details": None, "primary_outcomes": [],
             "effect_sizes": None, "secondary_outcomes": [],
             "author_conclusions": None, "funding_source": None, "coi_declared": None}
        ])
        assert (review_dir / "evidence_table.csv").exists()
        with (review_dir / "evidence_table.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1

    def test_status_updated_to_data_extracted(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_de_save(capsys, review_dir, [])
        saved = state_manager.load(review_dir)
        assert saved.status == ReviewStatus.data_extracted

    def test_partial_extractions_ok(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [
            _make_article(pmid="1"),
            _make_article(pmid="2"),
        ])
        _capture_de_save(capsys, review_dir, [
            {"orig_idx": 0, "study_design": "RCT", "sample_size": 100,
             "primary_outcomes": ["Readmission"], "secondary_outcomes": [],
             "follow_up_duration": None, "intervention_details": None,
             "comparison_details": None, "effect_sizes": None,
             "author_conclusions": None, "funding_source": None, "coi_declared": None}
            # orig_idx=1 has no extraction — fields stay null
        ])
        articles = json.loads((review_dir / "articles_included.json").read_text())
        assert articles[0]["study_design"] == "rct"
        assert articles[1]["study_design"] is None

    def test_returns_total_articles_count(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid=str(i)) for i in range(3)])
        out = _capture_de_save(capsys, review_dir, [])
        assert out["total_articles"] == 3

    def test_does_not_overwrite_existing_study_design(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1", study_design="Cohort")])
        _capture_de_save(capsys, review_dir, [
            {"orig_idx": 0, "study_design": "RCT", "sample_size": None,
             "primary_outcomes": [], "secondary_outcomes": [],
             "follow_up_duration": None, "intervention_details": None,
             "comparison_details": None, "effect_sizes": None,
             "author_conclusions": None, "funding_source": None, "coi_declared": None}
        ])
        articles = json.loads((review_dir / "articles_included.json").read_text())
        assert articles[0]["study_design"] == "Cohort"  # pre-existing design preserved

    def test_empty_included_set_produces_header_only_csv(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [])
        _capture_de_save(capsys, review_dir, [])
        with (review_dir / "evidence_table.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 0


