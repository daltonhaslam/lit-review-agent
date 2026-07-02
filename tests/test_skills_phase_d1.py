"""Unit tests for Phase D1 — batching support for data_extractor and quality_appraiser.

Tests the --batch-size / --batch-offset / --offset CLI pattern, batch accumulation,
and completion detection. Mirrors the abstract_screener batching tests in
test_skills_phase_c2.py. No LLM or API calls.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.data_extractor import prepare as de_prepare, save as de_save
from src.skills.quality_appraiser import prepare as qa_prepare, save as qa_save


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Adult patients hospitalized with heart failure",
        I="Care transitions interventions",
        C="Usual care",
        O=["30-day all-cause readmission rate", "mortality"],
        inclusion_criteria=["Adults ≥18", "Primary HF diagnosis"],
        exclusion_criteria=["Pediatric population", "Non-English"],
    )
    defaults.update(kwargs)
    return PICO(**defaults)


def _make_review(tmp_path: Path, status=ReviewStatus.fulltext_assessed, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "chf-2026-04"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="chf-2026-04",
        raw_question="Do care transitions reduce CHF readmission?",
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


def _make_article(title: str = "A CHF study", **kwargs) -> Article:
    defaults = dict(
        title=title,
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


def _capture_de_prepare(capsys, review_dir, batch_size=20, batch_offset=0) -> dict:
    de_prepare(review_dir, batch_size, batch_offset)
    return json.loads(capsys.readouterr().out)


def _capture_de_save(capsys, review_dir, extractions, batch_size=20, offset=0) -> dict:
    de_save(review_dir, json.dumps({"extractions": extractions}), batch_size, offset)
    return json.loads(capsys.readouterr().out)


def _capture_qa_prepare(capsys, review_dir, batch_size=20, batch_offset=0) -> dict:
    qa_prepare(review_dir, batch_size, batch_offset)
    return json.loads(capsys.readouterr().out)


def _capture_qa_save(capsys, review_dir, appraisals, batch_size=20, offset=0) -> dict:
    qa_save(review_dir, json.dumps({"appraisals": appraisals}), batch_size, offset)
    return json.loads(capsys.readouterr().out)


def _minimal_extraction(orig_idx: int) -> dict:
    return {
        "orig_idx": orig_idx,
        "study_design": "RCT",
        "sample_size": 100,
        "primary_outcomes": ["30-day readmission"],
        "secondary_outcomes": [],
        "follow_up_duration": "6 months",
        "intervention_details": "Nurse-led transitions",
        "comparison_details": "Usual care",
        "effect_sizes": "OR 0.7",
        "author_conclusions": "Effective",
        "funding_source": "NIH",
        "coi_declared": False,
    }


def _minimal_appraisal(orig_idx: int, score: float = 0.8) -> dict:
    return {"orig_idx": orig_idx, "score": score, "notes": "Adequate quality"}


# ===========================================================================
# TestDataExtractorBatching — prepare()
# ===========================================================================

class TestDataExtractorBatchingPrepare:
    def test_batch_number_starts_at_one(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_de_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert out["batch_number"] == 1

    def test_batch_number_increments_correctly(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(45)])
        out = _capture_de_prepare(capsys, review_dir, batch_size=20, batch_offset=20)
        assert out["batch_number"] == 2

    def test_total_batches_ceiling(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(45)])
        out = _capture_de_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert out["total_batches"] == math.ceil(45 / 20)

    def test_total_articles_is_full_count(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(37)])
        out = _capture_de_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert out["total_articles"] == 37

    def test_batch_contains_correct_count(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_de_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert len(out["articles"]) == 20

    def test_last_batch_is_partial(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_de_prepare(capsys, review_dir, batch_size=20, batch_offset=20)
        assert len(out["articles"]) == 5

    def test_orig_idx_equals_global_position(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_de_prepare(capsys, review_dir, batch_size=20, batch_offset=20)
        assert out["articles"][0]["orig_idx"] == 20
        assert out["articles"][4]["orig_idx"] == 24

    def test_dies_when_offset_out_of_range(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(5)])
        with pytest.raises(SystemExit):
            de_prepare(review_dir, batch_size=20, batch_offset=10)

    def test_second_batch_offset_matches_batch_size(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(40)])
        out = _capture_de_prepare(capsys, review_dir, batch_size=20, batch_offset=20)
        assert out["batch_offset"] == 20

    def test_extraction_task_present(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article()])
        out = _capture_de_prepare(capsys, review_dir)
        assert "extraction_task" in out
        assert "orig_idx" in out["extraction_task"]


# ===========================================================================
# TestDataExtractorBatching — save()
# ===========================================================================

class TestDataExtractorBatchingSave:
    def test_not_complete_on_partial_batch(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_de_save(capsys, review_dir, [], batch_size=20, offset=0)
        assert out["complete"] is False

    def test_complete_on_last_batch(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_de_save(capsys, review_dir, [], batch_size=20, offset=20)
        assert out["complete"] is True

    def test_complete_when_single_batch_covers_all(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(5)])
        out = _capture_de_save(capsys, review_dir, [], batch_size=20, offset=0)
        assert out["complete"] is True

    def test_status_not_updated_before_completion(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        _capture_de_save(capsys, review_dir, [], batch_size=20, offset=0)
        assert state_manager.load(review_dir).status == ReviewStatus.fulltext_assessed

    def test_status_updated_on_last_batch(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        _capture_de_save(capsys, review_dir, [], batch_size=20, offset=20)
        assert state_manager.load(review_dir).status == ReviewStatus.data_extracted

    def test_evidence_table_not_written_on_partial_batch(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        _capture_de_save(capsys, review_dir, [], batch_size=20, offset=0)
        assert not (review_dir / "evidence_table.csv").exists()

    def test_evidence_table_written_on_last_batch(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        _capture_de_save(capsys, review_dir, [], batch_size=20, offset=20)
        assert (review_dir / "evidence_table.csv").exists()

    def test_two_batches_accumulate_extractions(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(pmid=str(i)) for i in range(4)]
        _write_included(review_dir, articles)

        out1 = _capture_de_save(capsys, review_dir,
                                 [_minimal_extraction(0), _minimal_extraction(1)],
                                 batch_size=2, offset=0)
        assert out1["complete"] is False

        out2 = _capture_de_save(capsys, review_dir,
                                 [_minimal_extraction(2), _minimal_extraction(3)],
                                 batch_size=2, offset=2)
        assert out2["complete"] is True

    def test_earlier_extractions_preserved_across_saves(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(pmid=str(i)) for i in range(4)]
        _write_included(review_dir, articles)

        _capture_de_save(capsys, review_dir,
                          [_minimal_extraction(0)],
                          batch_size=2, offset=0)
        _capture_de_save(capsys, review_dir,
                          [_minimal_extraction(2), _minimal_extraction(3)],
                          batch_size=2, offset=2)

        saved = json.loads((review_dir / "articles_included.json").read_text())
        assert saved[0]["study_design"] == "rct"
        assert saved[2]["study_design"] == "rct"

    def test_batch_saved_count_in_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(5)])
        out = _capture_de_save(capsys, review_dir,
                                [_minimal_extraction(0), _minimal_extraction(1)],
                                batch_size=20, offset=0)
        assert out["batch_saved"] == 2

    def test_total_articles_in_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(7)])
        out = _capture_de_save(capsys, review_dir, [], batch_size=20, offset=0)
        assert out["total_articles"] == 7

    def test_evidence_table_path_in_complete_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid="1")])
        out = _capture_de_save(capsys, review_dir, [], batch_size=20, offset=0)
        assert "evidence_table" in out

    def test_evidence_table_key_absent_when_not_complete(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_de_save(capsys, review_dir, [], batch_size=20, offset=0)
        assert "evidence_table" not in out


# ===========================================================================
# TestQualityAppraiserBatching — prepare()
# ===========================================================================

class TestQualityAppraiserBatchingPrepare:
    def test_batch_number_starts_at_one(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_qa_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert out["batch_number"] == 1

    def test_batch_number_increments_correctly(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(45)])
        out = _capture_qa_prepare(capsys, review_dir, batch_size=20, batch_offset=20)
        assert out["batch_number"] == 2

    def test_total_batches_ceiling(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(45)])
        out = _capture_qa_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert out["total_batches"] == math.ceil(45 / 20)

    def test_total_articles_is_full_count(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(37)])
        out = _capture_qa_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert out["total_articles"] == 37

    def test_batch_contains_correct_count(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_qa_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert len(out["articles"]) == 20

    def test_last_batch_is_partial(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_qa_prepare(capsys, review_dir, batch_size=20, batch_offset=20)
        assert len(out["articles"]) == 5

    def test_orig_idx_equals_global_position(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_qa_prepare(capsys, review_dir, batch_size=20, batch_offset=20)
        assert out["articles"][0]["orig_idx"] == 20
        assert out["articles"][4]["orig_idx"] == 24

    def test_dies_when_offset_out_of_range(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(5)])
        with pytest.raises(SystemExit):
            qa_prepare(review_dir, batch_size=20, batch_offset=10)

    def test_instrument_detected_per_batch_article(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [
            _make_article(pmid="0", study_design="systematic review"),
            _make_article(pmid="1", study_design="RCT"),
        ])
        out = _capture_qa_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        instruments = {a["orig_idx"]: a["instrument"] for a in out["articles"]}
        assert instruments[0] == "AMSTAR-2"
        assert instruments[1] == "RoB 2.0"

    def test_appraisal_task_present(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article()])
        out = _capture_qa_prepare(capsys, review_dir)
        assert "appraisal_task" in out


# ===========================================================================
# TestQualityAppraiserBatching — save()
# ===========================================================================

class TestQualityAppraiserBatchingSave:
    def test_not_complete_on_partial_batch(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_qa_save(capsys, review_dir, [], batch_size=20, offset=0)
        assert out["complete"] is False

    def test_complete_on_last_batch(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_qa_save(capsys, review_dir, [], batch_size=20, offset=20)
        assert out["complete"] is True

    def test_status_not_updated_before_completion(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        _capture_qa_save(capsys, review_dir, [], batch_size=20, offset=0)
        assert state_manager.load(review_dir).status == ReviewStatus.enriched

    def test_status_updated_on_last_batch(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        _capture_qa_save(capsys, review_dir, [], batch_size=20, offset=20)
        assert state_manager.load(review_dir).status == ReviewStatus.quality_appraised

    def test_quality_report_not_written_on_partial_batch(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        _capture_qa_save(capsys, review_dir, [], batch_size=20, offset=0)
        assert not (review_dir / "quality_report.md").exists()

    def test_quality_report_written_on_last_batch(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        _capture_qa_save(capsys, review_dir, [], batch_size=20, offset=20)
        assert (review_dir / "quality_report.md").exists()

    def test_two_batches_accumulate_appraisals(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(4)])

        out1 = _capture_qa_save(capsys, review_dir,
                                  [_minimal_appraisal(0), _minimal_appraisal(1)],
                                  batch_size=2, offset=0)
        assert out1["complete"] is False

        out2 = _capture_qa_save(capsys, review_dir,
                                  [_minimal_appraisal(2), _minimal_appraisal(3)],
                                  batch_size=2, offset=2)
        assert out2["complete"] is True

    def test_earlier_appraisals_preserved_across_saves(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i), study_design="RCT") for i in range(4)])

        _capture_qa_save(capsys, review_dir,
                          [_minimal_appraisal(0, 0.9), _minimal_appraisal(1, 0.8)],
                          batch_size=2, offset=0)
        _capture_qa_save(capsys, review_dir,
                          [_minimal_appraisal(2, 0.7), _minimal_appraisal(3, 0.6)],
                          batch_size=2, offset=2)

        saved = json.loads((review_dir / "articles_included.json").read_text())
        assert saved[0]["quality_score"] == pytest.approx(0.9)
        assert saved[1]["quality_score"] == pytest.approx(0.8)
        assert saved[2]["quality_score"] == pytest.approx(0.7)
        assert saved[3]["quality_score"] == pytest.approx(0.6)

    def test_second_batch_does_not_overwrite_first_batch_scores(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i), study_design="RCT") for i in range(4)])

        _capture_qa_save(capsys, review_dir,
                          [_minimal_appraisal(0, 0.9), _minimal_appraisal(1, 0.8)],
                          batch_size=2, offset=0)
        _capture_qa_save(capsys, review_dir, [], batch_size=2, offset=2)

        saved = json.loads((review_dir / "articles_included.json").read_text())
        assert saved[0]["quality_score"] == pytest.approx(0.9)
        assert saved[1]["quality_score"] == pytest.approx(0.8)

    def test_by_instrument_accumulates_across_batches(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [
            _make_article(pmid="0", study_design="systematic review"),
            _make_article(pmid="1", study_design="systematic review"),
            _make_article(pmid="2", study_design="RCT"),
            _make_article(pmid="3", study_design="RCT"),
        ])

        _capture_qa_save(capsys, review_dir,
                          [_minimal_appraisal(0), _minimal_appraisal(1)],
                          batch_size=2, offset=0)
        out = _capture_qa_save(capsys, review_dir,
                                 [_minimal_appraisal(2), _minimal_appraisal(3)],
                                 batch_size=2, offset=2)

        assert out["by_instrument"].get("AMSTAR-2", 0) == 2
        assert out["by_instrument"].get("RoB 2.0", 0) == 2

    def test_batch_saved_count_in_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(5)])
        out = _capture_qa_save(capsys, review_dir,
                                [_minimal_appraisal(0), _minimal_appraisal(1)],
                                batch_size=20, offset=0)
        assert out["batch_saved"] == 2

    def test_quality_report_key_in_complete_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid="1")])
        out = _capture_qa_save(capsys, review_dir, [], batch_size=20, offset=0)
        assert "quality_report" in out

    def test_quality_report_key_absent_when_not_complete(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_qa_save(capsys, review_dir, [], batch_size=20, offset=0)
        assert "quality_report" not in out
