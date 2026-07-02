"""Phase H — Stages 11 & 13 parallel sub-agent extraction/appraisal (Issue #37 cont.).

Covers slice-mode prepare() for both data_extractor and quality_appraiser:
partitioning math, slice metadata, validation, and the merged --save accepting
extractions/appraisals across slices in a single call. Also asserts the bumped
default batch size on both modules.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills import data_extractor, quality_appraiser
from src.skills.data_extractor import (
    BATCH_SIZE_DEFAULT as DE_BATCH_DEFAULT,
    prepare as de_prepare,
    save as de_save,
)
from src.skills.quality_appraiser import (
    BATCH_SIZE_DEFAULT as QA_BATCH_DEFAULT,
    prepare as qa_prepare,
    save as qa_save,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pico() -> PICO:
    return PICO(
        P="Adult patients hospitalized with heart failure",
        I="Care transitions interventions",
        C="Usual care",
        O=["30-day all-cause readmission rate"],
        inclusion_criteria=["Adults ≥18"],
        exclusion_criteria=["Pediatric"],
    )


def _make_review(tmp_path: Path, status: ReviewStatus) -> tuple[Review, Path]:
    review_dir = tmp_path / "chf-2026-04"
    review_dir.mkdir(exist_ok=True)
    review = Review(
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
    state_manager.save(review, review_dir)
    return review, review_dir


def _make_article(idx: int, study_design: str = "RCT") -> Article:
    return Article(
        title=f"Study {idx}",
        authors=["Smith J"],
        year=2022,
        journal="NEJM",
        pmid=str(idx),
        source_dbs=["pubmed"],
        screening_decision=ScreeningDecision.include,
        study_design=study_design,
    )


def _write_included(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_included.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _capture_de_prepare(capsys, review_dir, **kwargs) -> dict:
    de_prepare(
        review_dir,
        kwargs.pop("batch_size", DE_BATCH_DEFAULT),
        kwargs.pop("batch_offset", 0),
        slice_of=kwargs.pop("slice_of", None),
        slice_index=kwargs.pop("slice_index", None),
    )
    return json.loads(capsys.readouterr().out)


def _capture_de_save(capsys, review_dir, extractions, **kwargs) -> dict:
    de_save(
        review_dir,
        json.dumps({"extractions": extractions}),
        kwargs.pop("batch_size", DE_BATCH_DEFAULT),
        kwargs.pop("offset", 0),
    )
    return json.loads(capsys.readouterr().out)


def _capture_qa_prepare(capsys, review_dir, **kwargs) -> dict:
    qa_prepare(
        review_dir,
        kwargs.pop("batch_size", QA_BATCH_DEFAULT),
        kwargs.pop("batch_offset", 0),
        slice_of=kwargs.pop("slice_of", None),
        slice_index=kwargs.pop("slice_index", None),
    )
    return json.loads(capsys.readouterr().out)


def _capture_qa_save(capsys, review_dir, appraisals, **kwargs) -> dict:
    qa_save(
        review_dir,
        json.dumps({"appraisals": appraisals}),
        kwargs.pop("batch_size", QA_BATCH_DEFAULT),
        kwargs.pop("offset", 0),
    )
    return json.loads(capsys.readouterr().out)


def _full_extraction(orig_idx: int) -> dict:
    return {
        "orig_idx": orig_idx,
        "study_design": "RCT",
        "sample_size": 100,
        "primary_outcomes": ["30-day readmission"],
        "secondary_outcomes": [],
        "follow_up_duration": "6 months",
        "intervention_details": "Nurse-led",
        "comparison_details": "Usual care",
        "effect_sizes": "OR 0.7",
        "author_conclusions": "Effective",
        "funding_source": "NIH",
        "coi_declared": False,
    }


def _full_appraisal(orig_idx: int) -> dict:
    return {"orig_idx": orig_idx, "score": 0.8, "notes": "Adequate"}


# ---------------------------------------------------------------------------
# BATCH_SIZE_DEFAULT bump
# ---------------------------------------------------------------------------

class TestBatchSizeDefaults:
    def test_data_extractor_default_is_50(self):
        assert data_extractor.BATCH_SIZE_DEFAULT == 50

    def test_quality_appraiser_default_is_50(self):
        assert quality_appraiser.BATCH_SIZE_DEFAULT == 50


# ---------------------------------------------------------------------------
# Slice partitioning math — data_extractor
# ---------------------------------------------------------------------------

class TestDataExtractorSlicePartitioning:
    def test_5_slices_over_201_balanced_within_one(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(i) for i in range(201)])
        sizes: list[int] = []
        all_indices: list[int] = []
        for i in range(5):
            out = _capture_de_prepare(capsys, review_dir, slice_of=5, slice_index=i)
            sizes.append(out["slice_pending"])
            all_indices.extend(out["slice_indices"])
        assert max(sizes) - min(sizes) <= 1
        assert sum(sizes) == 201
        assert sorted(all_indices) == list(range(201))

    def test_slice_indices_are_contiguous_per_slice(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(i) for i in range(80)])
        for i in range(4):
            out = _capture_de_prepare(capsys, review_dir, slice_of=4, slice_index=i)
            indices = out["slice_indices"]
            assert indices == list(range(indices[0], indices[-1] + 1))

    def test_slice_metadata_present_only_in_slice_mode(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(i) for i in range(20)])

        serial = _capture_de_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        for key in ("slice_of", "slice_index", "slice_pending", "slice_indices"):
            assert key not in serial

        slice_out = _capture_de_prepare(capsys, review_dir, slice_of=2, slice_index=0)
        assert slice_out["slice_of"] == 2
        assert slice_out["slice_index"] == 0
        assert slice_out["slice_pending"] == len(slice_out["articles"])
        assert slice_out["slice_indices"] == [a["orig_idx"] for a in slice_out["articles"]]

    def test_extraction_task_present_in_every_slice(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(i) for i in range(30)])
        for i in range(3):
            out = _capture_de_prepare(capsys, review_dir, slice_of=3, slice_index=i)
            assert "extraction_task" in out
            assert "orig_idx" in out["extraction_task"]


# ---------------------------------------------------------------------------
# Slice partitioning math — quality_appraiser
# ---------------------------------------------------------------------------

class TestQualityAppraiserSlicePartitioning:
    def test_3_slices_over_100_balanced_within_one(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(i) for i in range(100)])
        sizes: list[int] = []
        all_indices: list[int] = []
        for i in range(3):
            out = _capture_qa_prepare(capsys, review_dir, slice_of=3, slice_index=i)
            sizes.append(out["slice_pending"])
            all_indices.extend(out["slice_indices"])
        assert max(sizes) - min(sizes) <= 1
        assert sum(sizes) == 100
        assert sorted(all_indices) == list(range(100))

    def test_appraisal_task_and_per_article_prompt_present_in_every_slice(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.enriched)
        _write_included(review_dir, [
            _make_article(i, study_design="RCT" if i % 2 == 0 else "systematic review")
            for i in range(20)
        ])
        for i in range(2):
            out = _capture_qa_prepare(capsys, review_dir, slice_of=2, slice_index=i)
            assert "appraisal_task" in out
            for art in out["articles"]:
                assert "instrument" in art
                assert "appraisal_prompt" in art

    def test_slice_metadata_present_only_in_slice_mode(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(i) for i in range(20)])

        serial = _capture_qa_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        for key in ("slice_of", "slice_index", "slice_pending", "slice_indices"):
            assert key not in serial

        slice_out = _capture_qa_prepare(capsys, review_dir, slice_of=2, slice_index=1)
        assert slice_out["slice_of"] == 2
        assert slice_out["slice_index"] == 1
        assert slice_out["slice_pending"] == len(slice_out["articles"])


# ---------------------------------------------------------------------------
# Slice spec validation (shared logic)
# ---------------------------------------------------------------------------

class TestSliceValidation:
    def test_de_slice_of_without_slice_index_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(0)])
        with pytest.raises(SystemExit):
            de_prepare(review_dir, DE_BATCH_DEFAULT, 0, slice_of=2, slice_index=None)

    def test_de_slice_index_without_slice_of_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(0)])
        with pytest.raises(SystemExit):
            de_prepare(review_dir, DE_BATCH_DEFAULT, 0, slice_of=None, slice_index=0)

    def test_de_slice_index_out_of_range_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(i) for i in range(10)])
        with pytest.raises(SystemExit):
            de_prepare(review_dir, DE_BATCH_DEFAULT, 0, slice_of=3, slice_index=3)

    def test_de_slice_of_zero_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(i) for i in range(10)])
        with pytest.raises(SystemExit):
            de_prepare(review_dir, DE_BATCH_DEFAULT, 0, slice_of=0, slice_index=0)

    def test_qa_slice_of_without_slice_index_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path, ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(0)])
        with pytest.raises(SystemExit):
            qa_prepare(review_dir, QA_BATCH_DEFAULT, 0, slice_of=2, slice_index=None)

    def test_qa_slice_index_out_of_range_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path, ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(i) for i in range(10)])
        with pytest.raises(SystemExit):
            qa_prepare(review_dir, QA_BATCH_DEFAULT, 0, slice_of=3, slice_index=5)


# ---------------------------------------------------------------------------
# Merged save covers all slices — data_extractor
# ---------------------------------------------------------------------------

class TestDataExtractorMergedSave:
    def test_one_shot_save_covers_all_articles_across_slices(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(i) for i in range(60)])

        merged: list[dict] = []
        for i in range(3):
            out = _capture_de_prepare(capsys, review_dir, slice_of=3, slice_index=i)
            for idx in out["slice_indices"]:
                merged.append(_full_extraction(idx))

        # Default batch_size=50 so positional check fails (50<60); only the
        # all-coverage rule can complete the save.
        result = _capture_de_save(capsys, review_dir, merged, batch_size=50, offset=0)
        assert result["complete"] is True
        assert (review_dir / "evidence_table.csv").exists()
        assert state_manager.load(review_dir).status == ReviewStatus.data_extracted

        saved = json.loads((review_dir / "articles_included.json").read_text())
        assert all(a["study_design"] == "RCT" for a in saved)

    def test_save_not_complete_when_extractions_partial_and_batch_partial(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(i) for i in range(60)])

        # Only 30 of 60 extractions, batch_size=50 → neither rule fires
        merged = [_full_extraction(i) for i in range(30)]
        result = _capture_de_save(capsys, review_dir, merged, batch_size=50, offset=0)
        assert result["complete"] is False


# ---------------------------------------------------------------------------
# Merged save covers all slices — quality_appraiser
# ---------------------------------------------------------------------------

class TestQualityAppraiserMergedSave:
    def test_one_shot_save_covers_all_articles_across_slices(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.enriched)
        _write_included(review_dir, [
            _make_article(i, study_design="RCT" if i % 2 == 0 else "systematic review")
            for i in range(60)
        ])

        merged: list[dict] = []
        for i in range(3):
            out = _capture_qa_prepare(capsys, review_dir, slice_of=3, slice_index=i)
            for idx in out["slice_indices"]:
                merged.append(_full_appraisal(idx))

        result = _capture_qa_save(capsys, review_dir, merged, batch_size=50, offset=0)
        assert result["complete"] is True
        assert (review_dir / "quality_report.md").exists()
        assert state_manager.load(review_dir).status == ReviewStatus.quality_appraised

        saved = json.loads((review_dir / "articles_included.json").read_text())
        assert all(a["quality_score"] == pytest.approx(0.8) for a in saved)
        # Both instruments represented
        instruments = {a["quality_instrument"] for a in saved}
        assert instruments == {"RoB 2.0", "AMSTAR-2"}

    def test_save_not_complete_when_appraisals_partial_and_batch_partial(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(i) for i in range(60)])

        merged = [_full_appraisal(i) for i in range(30)]
        result = _capture_qa_save(capsys, review_dir, merged, batch_size=50, offset=0)
        assert result["complete"] is False
