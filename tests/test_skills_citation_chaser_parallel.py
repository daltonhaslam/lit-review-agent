"""Phase 3 — Stage 8.5 citation-chase parallel screening.

Covers slice-mode prepare(): partitioning math, idempotent reuse of the
persisted _citation_candidates.json file, slice validation, and the merged
--save accepting decisions across slices (including out-of-order).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.agent import state as state_manager
from src.models.article import Article
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType
from src.skills.citation_chaser import (
    _CANDIDATES_FILE as CC_CANDIDATES_FILE,
    prepare as cc_prepare,
    save as cc_save,
)


def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Adult patients hospitalized with heart failure",
        I="Care transitions interventions",
        C="Usual care",
        O=["30-day all-cause readmission"],
        inclusion_criteria=["Adults ≥18"],
        exclusion_criteria=["Pediatric"],
    )
    defaults.update(kwargs)
    return PICO(**defaults)


def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "cc-parallel-review"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="cc-parallel-review",
        raw_question="Do care transitions reduce CHF readmission?",
        review_type=ReviewType.systematic,
        status=ReviewStatus.abstract_screening,
        pico=_make_pico(),
        queries=QuerySet(
            pubmed=[Query(step_number="#1", query_string='"Heart Failure"[Mesh]', database="pubmed")],
        ),
    )
    defaults.update(kwargs)
    review = Review(**defaults)
    state_manager.save(review, review_dir)
    return review, review_dir


def _make_article(idx: int, **kwargs) -> Article:
    defaults = dict(
        title=f"Citation-chased paper #{idx}",
        authors=["Smith J"],
        year=2022,
        journal="NEJM",
        doi=f"10.1/cc-{idx}",
        source_dbs=["crossref"],
    )
    defaults.update(kwargs)
    return Article(**defaults)


def _write_candidates(
    review_dir: Path,
    candidates: list[Article],
    backward_count: int = 0,
    forward_count: int = 0,
    pre_filter_total: int | None = None,
) -> None:
    (review_dir / CC_CANDIDATES_FILE).write_text(
        json.dumps({
            "backward_count": backward_count,
            "forward_count": forward_count,
            "pre_filter_total": pre_filter_total if pre_filter_total is not None else len(candidates),
            "pre_filtered_count": 0,
            "filter_info": {"skipped": True, "reason": "test"},
            "enrichment": {},
            "candidates": [a.model_dump(mode="json") for a in candidates],
        }, indent=2),
        encoding="utf-8",
    )


def _capture_prepare(capsys, review_dir, slice_of=None, slice_index=None) -> dict:
    cc_prepare(review_dir, slice_of=slice_of, slice_index=slice_index)
    return json.loads(capsys.readouterr().out)


def _capture_save(capsys, review_dir, decisions) -> dict:
    cc_save(review_dir, json.dumps({"decisions": decisions}))
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# Slice partitioning math
# ---------------------------------------------------------------------------

class TestSlicePartitioning:
    def test_5_slices_over_1001_balanced_within_one(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(1001)])

        sizes: list[int] = []
        all_indices: list[int] = []
        for i in range(5):
            out = _capture_prepare(capsys, review_dir, slice_of=5, slice_index=i)
            sizes.append(out["slice_pending"])
            all_indices.extend(out["slice_indices"])
        assert max(sizes) - min(sizes) <= 1
        assert sum(sizes) == 1001
        # Disjoint, covers every candidate index exactly once.
        assert sorted(all_indices) == list(range(1001))

    def test_slice_indices_are_contiguous_per_slice(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(100)])
        for i in range(4):
            out = _capture_prepare(capsys, review_dir, slice_of=4, slice_index=i)
            indices = out["slice_indices"]
            assert indices == list(range(indices[0], indices[-1] + 1))

    def test_slice_metadata_present_only_in_slice_mode(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(20)])

        full = _capture_prepare(capsys, review_dir)
        assert "slice_of" not in full
        assert "slice_index" not in full
        assert "slice_pending" not in full
        assert "slice_indices" not in full
        # Even in non-slice mode, total_candidates is exposed for the skill.
        assert full["total_candidates"] == 20

        slice_out = _capture_prepare(capsys, review_dir, slice_of=2, slice_index=0)
        assert slice_out["slice_of"] == 2
        assert slice_out["slice_index"] == 0
        assert slice_out["slice_pending"] == len(slice_out["candidates"])
        assert slice_out["slice_indices"] == [c["index"] for c in slice_out["candidates"]]

    def test_candidate_indices_match_slice_indices(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(50)])
        out = _capture_prepare(capsys, review_dir, slice_of=3, slice_index=1)
        candidate_indices = [c["index"] for c in out["candidates"]]
        assert candidate_indices == out["slice_indices"]

    def test_slice_of_1_returns_full_set(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(30)])
        out = _capture_prepare(capsys, review_dir, slice_of=1, slice_index=0)
        assert out["slice_pending"] == 30
        assert out["slice_indices"] == list(range(30))


# ---------------------------------------------------------------------------
# Idempotent reuse of _CANDIDATES_FILE
# ---------------------------------------------------------------------------

class TestPersistedFileReuse:
    def test_existing_candidates_file_skips_network(self, tmp_path, capsys):
        """When _CANDIDATES_FILE exists, prepare() must NOT re-run the async pipeline."""
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(10)])

        with patch("src.skills.citation_chaser._prepare_async") as mock_async:
            out = _capture_prepare(capsys, review_dir, slice_of=2, slice_index=0)

        mock_async.assert_not_called()
        assert out["total_candidates"] == 10
        assert out["slice_pending"] == 5

    def test_parallel_slice_calls_return_consistent_total(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(40)])

        slice_outs = [
            _capture_prepare(capsys, review_dir, slice_of=4, slice_index=i)
            for i in range(4)
        ]
        # All slices read the same persisted file → identical totals.
        for out in slice_outs:
            assert out["total_candidates"] == 40

    def test_pico_context_and_screening_task_emitted_on_every_slice(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(20)])
        for i in range(4):
            out = _capture_prepare(capsys, review_dir, slice_of=4, slice_index=i)
            assert "P (Population)" in out["pico_context"]
            assert "screening_task" in out and out["screening_task"]


# ---------------------------------------------------------------------------
# Slice spec validation
# ---------------------------------------------------------------------------

class TestSliceValidation:
    def test_slice_of_without_slice_index_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(0)])
        with pytest.raises(SystemExit):
            cc_prepare(review_dir, slice_of=2, slice_index=None)

    def test_slice_index_without_slice_of_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(0)])
        with pytest.raises(SystemExit):
            cc_prepare(review_dir, slice_of=None, slice_index=0)

    def test_slice_of_zero_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(10)])
        with pytest.raises(SystemExit):
            cc_prepare(review_dir, slice_of=0, slice_index=0)

    def test_slice_index_out_of_range_dies(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(10)])
        with pytest.raises(SystemExit):
            cc_prepare(review_dir, slice_of=3, slice_index=3)

    def test_slice_mode_without_candidates_file_dies(self, tmp_path):
        """Defensive: refusing slice mode without the persisted file prevents N parallel
        slice calls from racing the network pipeline if the skill skipped Step 1."""
        _, review_dir = _make_review(tmp_path)
        with patch("src.skills.citation_chaser._prepare_async") as mock_async:
            with pytest.raises(SystemExit):
                cc_prepare(review_dir, slice_of=2, slice_index=0)
        mock_async.assert_not_called()


# ---------------------------------------------------------------------------
# Merged save covers all candidates
# ---------------------------------------------------------------------------

_NO_METADATA_STATS = {"total": 0}


class TestMergedSave:
    @pytest.fixture(autouse=True)
    def _patch_metadata_enrichment(self):
        """Issue #49: save() now calls _enrich_missing_metadata via asyncio.run.
        Patch it out so these integration tests don't make real network calls."""
        with patch(
            "src.skills.citation_chaser._enrich_missing_metadata",
            new=AsyncMock(return_value=_NO_METADATA_STATS),
        ):
            yield

    def test_one_shot_save_covers_all_candidates_across_slices(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(60)])

        merged: list[dict] = []
        for slice_i in range(3):
            out = _capture_prepare(capsys, review_dir, slice_of=3, slice_index=slice_i)
            for idx in out["slice_indices"]:
                merged.append({"index": idx, "decision": "include", "rationale": "Relevant"})

        result = _capture_save(capsys, review_dir, merged)
        assert result["after_dedup"] == 60
        assert result["new_includes"] == 60

        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert len(screened) == 60
        for a in screened:
            assert a["citation_chased"] is True
            assert a["screening_decision"] == "include"

    def test_merged_save_handles_mixed_decisions(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(15)])

        merged: list[dict] = []
        for slice_i in range(3):
            out = _capture_prepare(capsys, review_dir, slice_of=3, slice_index=slice_i)
            for idx in out["slice_indices"]:
                dec = "include" if idx % 3 == 0 else ("borderline" if idx % 3 == 1 else "exclude")
                merged.append({"index": idx, "decision": dec, "rationale": "test"})

        result = _capture_save(capsys, review_dir, merged)
        # Includes: 0,3,6,9,12 = 5; borderlines: 1,4,7,10,13 = 5; excludes: 2,5,8,11,14 = 5.
        assert result["new_includes"] == 5
        assert result["new_borderlines"] == 5

        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.citation_chasing_added == 5
        assert saved.prisma_counts.citation_chasing_borderlines == 5

        # Excludes are not appended to articles_screened.json — only includes + borderlines.
        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert len(screened) == 10

    def test_save_accepts_out_of_order_decisions(self, tmp_path, capsys):
        """Parallel sub-agents may return slice results in arbitrary order."""
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(12)])

        # Build decisions in reversed slice order, then shuffle within.
        merged: list[dict] = []
        slice_results = [
            _capture_prepare(capsys, review_dir, slice_of=3, slice_index=i) for i in range(3)
        ]
        for out in reversed(slice_results):
            chunk = [
                {"index": idx, "decision": "include", "rationale": "Relevant"}
                for idx in reversed(out["slice_indices"])
            ]
            merged.extend(chunk)

        result = _capture_save(capsys, review_dir, merged)
        assert result["after_dedup"] == 12
        assert result["new_includes"] == 12
