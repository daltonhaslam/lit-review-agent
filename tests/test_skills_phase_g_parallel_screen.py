"""Phase G — Stage 8 parallel sub-agent screening (Issue #37).

Covers slice-mode prepare(): partitioning math, auto-resume interaction with
slices, the merged --save accepting decisions across slices, and the bumped
default batch size.
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
from src.skills import abstract_screener
from src.skills.abstract_screener import BATCH_SIZE_DEFAULT, prepare, save


def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Adult patients hospitalized with heart failure",
        I="Care transitions interventions",
        C="Usual care",
        O=["30-day all-cause readmission rate"],
        inclusion_criteria=["Adults ≥18"],
        exclusion_criteria=["Pediatric"],
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
        status=ReviewStatus.deduplicated,
        pico=_make_pico(),
        queries=QuerySet(
            pubmed=[Query(step_number="#1", query_string='"Heart Failure"[Mesh]', database="pubmed")],
        ),
    )
    defaults.update(kwargs)
    review = Review(**defaults)
    state_manager.save(review, review_dir)
    return review, review_dir


def _make_article(title: str = "A study", **kwargs) -> Article:
    defaults = dict(title=title, authors=["Smith J"], year=2022, journal="NEJM", source_dbs=["pubmed"])
    defaults.update(kwargs)
    return Article(**defaults)


def _write_deduped(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_deduped.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _capture_prepare(capsys, review_dir, **kwargs) -> dict:
    prepare(
        review_dir,
        kwargs.pop("batch_size", BATCH_SIZE_DEFAULT),
        kwargs.pop("batch_offset", 0),
        slice_of=kwargs.pop("slice_of", None),
        slice_index=kwargs.pop("slice_index", None),
    )
    return json.loads(capsys.readouterr().out)


def _capture_save(capsys, review_dir, offset, decisions) -> dict:
    save(review_dir, offset, json.dumps({"decisions": decisions}))
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# BATCH_SIZE_DEFAULT bump
# ---------------------------------------------------------------------------

class TestBatchSizeDefault:
    def test_batch_size_default_is_50(self):
        assert abstract_screener.BATCH_SIZE_DEFAULT == 50


# ---------------------------------------------------------------------------
# Slice partitioning math
# ---------------------------------------------------------------------------

class TestSlicePartitioning:
    def test_5_slices_over_1001_balanced_within_one(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid=str(i)) for i in range(1001)])
        sizes = []
        all_indices: list[int] = []
        for i in range(5):
            out = _capture_prepare(capsys, review_dir, slice_of=5, slice_index=i)
            sizes.append(out["slice_pending"])
            all_indices.extend(out["slice_indices"])
        assert max(sizes) - min(sizes) <= 1
        assert sum(sizes) == 1001
        # Disjoint, covers every pending index exactly once
        assert sorted(all_indices) == list(range(1001))

    def test_slice_indices_are_contiguous_per_slice(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid=str(i)) for i in range(100)])
        for i in range(4):
            out = _capture_prepare(capsys, review_dir, slice_of=4, slice_index=i)
            indices = out["slice_indices"]
            assert indices == list(range(indices[0], indices[-1] + 1))

    def test_slice_metadata_present_only_in_slice_mode(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid=str(i)) for i in range(20)])

        serial = _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert "slice_of" not in serial
        assert "slice_index" not in serial
        assert "slice_pending" not in serial
        assert "slice_indices" not in serial

        slice_out = _capture_prepare(capsys, review_dir, slice_of=2, slice_index=0)
        assert slice_out["slice_of"] == 2
        assert slice_out["slice_index"] == 0
        assert slice_out["slice_pending"] == len(slice_out["articles"])
        assert slice_out["slice_indices"] == [a["article_index"] for a in slice_out["articles"]]

    def test_articles_match_slice_indices(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid=str(i)) for i in range(50)])
        out = _capture_prepare(capsys, review_dir, slice_of=3, slice_index=1)
        article_indices = [a["article_index"] for a in out["articles"]]
        assert article_indices == out["slice_indices"]

    def test_slice_of_1_returns_full_pending(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid=str(i)) for i in range(30)])
        out = _capture_prepare(capsys, review_dir, slice_of=1, slice_index=0)
        assert out["slice_pending"] == 30
        assert out["slice_indices"] == list(range(30))


# ---------------------------------------------------------------------------
# Slice mode + auto-resume
# ---------------------------------------------------------------------------

class TestSliceWithAutoResume:
    def test_slices_partition_only_remaining_pending(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(pmid=str(i)) for i in range(20)]
        _write_deduped(review_dir, articles)

        # Pre-decide articles 0-9 via a save (simulating a prior run)
        decided = [
            {"article_index": i, "decision": "include", "reason": "ok", "reason_category": None}
            for i in range(10)
        ]
        _capture_save(capsys, review_dir, 0, decided)

        # Now slice into 2 — should split the remaining 10 (indices 10-19)
        sizes: list[int] = []
        all_indices: list[int] = []
        for i in range(2):
            out = _capture_prepare(capsys, review_dir, slice_of=2, slice_index=i)
            sizes.append(out["slice_pending"])
            all_indices.extend(out["slice_indices"])
            assert out["auto_resume_offset"] == 10
        assert sum(sizes) == 10
        assert sorted(all_indices) == list(range(10, 20))

    def test_slice_excludes_date_prefiltered_articles(self, tmp_path, capsys):
        from src.models.review import DateRange

        review, review_dir = _make_review(tmp_path)
        review.date_range = DateRange(start=2020, end=2025)
        state_manager.save(review, review_dir)

        articles = [_make_article(pmid=str(i), year=2010 if i < 5 else 2022) for i in range(15)]
        _write_deduped(review_dir, articles)

        all_indices: list[int] = []
        for i in range(2):
            out = _capture_prepare(capsys, review_dir, slice_of=2, slice_index=i)
            all_indices.extend(out["slice_indices"])
        # Articles 0-4 should be auto-excluded by date prefilter; slices cover 5-14 only
        assert sorted(all_indices) == list(range(5, 15))

    def test_slice_mode_does_not_persist_screened_file(self, tmp_path, capsys):
        # The date-prefilter writeback must be skipped in slice mode — N parallel slices would
        # each do a full read-modify-write of articles_screened.json and race/tear it. The
        # non-sliced Step-1 probe persists the date exclusions instead.
        from src.models.review import DateRange

        review, review_dir = _make_review(tmp_path)
        review.date_range = DateRange(start=2020, end=2025)
        state_manager.save(review, review_dir)
        # article 0 is out of range → would trigger the prefilter write in the buggy path
        _write_deduped(review_dir, [_make_article(pmid=str(i), year=2010 if i == 0 else 2022) for i in range(6)])

        _capture_prepare(capsys, review_dir, slice_of=2, slice_index=0)
        assert not (review_dir / "articles_screened.json").exists()

    def test_non_slice_probe_persists_date_exclusions(self, tmp_path, capsys):
        # The non-sliced probe MUST still persist date exclusions (the guard only skips slices).
        from src.models.review import DateRange

        review, review_dir = _make_review(tmp_path)
        review.date_range = DateRange(start=2020, end=2025)
        state_manager.save(review, review_dir)
        _write_deduped(review_dir, [_make_article(pmid=str(i), year=2010 if i == 0 else 2022) for i in range(6)])

        _capture_prepare(capsys, review_dir, batch_size=1)  # non-slice probe
        assert (review_dir / "articles_screened.json").exists()


# ---------------------------------------------------------------------------
# Slice spec validation
# ---------------------------------------------------------------------------

class TestSliceValidation:
    def test_slice_of_without_slice_index_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()])
        with pytest.raises(SystemExit):
            prepare(review_dir, BATCH_SIZE_DEFAULT, 0, slice_of=2, slice_index=None)

    def test_slice_index_without_slice_of_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()])
        with pytest.raises(SystemExit):
            prepare(review_dir, BATCH_SIZE_DEFAULT, 0, slice_of=None, slice_index=0)

    def test_slice_index_out_of_range_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid=str(i)) for i in range(10)])
        with pytest.raises(SystemExit):
            prepare(review_dir, BATCH_SIZE_DEFAULT, 0, slice_of=3, slice_index=3)

    def test_slice_of_zero_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid=str(i)) for i in range(10)])
        with pytest.raises(SystemExit):
            prepare(review_dir, BATCH_SIZE_DEFAULT, 0, slice_of=0, slice_index=0)


# ---------------------------------------------------------------------------
# Merged save covers all slices
# ---------------------------------------------------------------------------

class TestMergedSave:
    def test_one_shot_save_covers_all_articles_across_slices(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid=str(i)) for i in range(60)])

        # Collect indices from each slice
        merged: list[dict] = []
        for i in range(3):
            out = _capture_prepare(capsys, review_dir, slice_of=3, slice_index=i)
            for idx in out["slice_indices"]:
                merged.append({
                    "article_index": idx,
                    "decision": "include",
                    "reason": "Relevant",
                    "reason_category": None,
                })

        result = _capture_save(capsys, review_dir, 0, merged)
        assert result["complete"] is True
        assert result["pending_remaining"] == 0
        assert result["include"] == 60

        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert len(screened) == 60
        for a in screened:
            assert a["screening_decision"] == "include"

    def test_merged_save_handles_mixed_decisions_no_holes(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid=str(i)) for i in range(15)])

        merged: list[dict] = []
        for slice_i in range(3):
            out = _capture_prepare(capsys, review_dir, slice_of=3, slice_index=slice_i)
            for idx in out["slice_indices"]:
                # alternate decisions to verify all paths
                dec = "include" if idx % 3 == 0 else ("borderline" if idx % 3 == 1 else "exclude")
                cat = None if dec != "exclude" else "wrong_population"
                merged.append({
                    "article_index": idx,
                    "decision": dec,
                    "reason": "test",
                    "reason_category": cat,
                })

        result = _capture_save(capsys, review_dir, 0, merged)
        assert result["complete"] is True
        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert all(a["screening_decision"] != "pending" for a in screened)

        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.screened == 15
        assert saved.prisma_counts.eligibility == result["include"] + result["borderline"]
