"""Unit tests for state manager (Phase 0)."""
import json
import pytest
from pathlib import Path

from src.agent.state import load, save, scan, _safe_review_dir
from src.models.review import Review, ReviewType, ReviewStatus


def _make_review(review_id: str = "test-review-2026") -> Review:
    return Review(
        review_id=review_id,
        review_type=ReviewType.systematic,
        raw_question="Does early mobility reduce LOS in ICU patients?",
        status=ReviewStatus.pico_formulated,
    )


# --- save / load ---

def test_save_creates_state_json(tmp_path):
    review = _make_review()
    review_dir = tmp_path / "test-review-2026"
    save(review, review_dir)
    assert (review_dir / "state.json").exists()


def test_save_load_roundtrip(tmp_path):
    review = _make_review()
    review.prisma_counts.identified = 150
    review_dir = tmp_path / "test-review-2026"
    save(review, review_dir)

    loaded = load(review_dir)
    assert loaded.review_id == "test-review-2026"
    assert loaded.review_type == ReviewType.systematic
    assert loaded.prisma_counts.identified == 150
    assert loaded.status == ReviewStatus.pico_formulated


def test_save_is_atomic(tmp_path):
    """save() should leave no temp files behind."""
    review = _make_review()
    review_dir = tmp_path / "test-review-2026"
    save(review, review_dir)
    tmp_files = list(review_dir.glob("*.tmp"))
    assert tmp_files == []


def test_save_updates_updated_at(tmp_path):
    review = _make_review()
    original_ts = review.updated_at
    review_dir = tmp_path / "test-review-2026"
    save(review, review_dir)
    loaded = load(review_dir)
    assert loaded.updated_at >= original_ts


def test_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "nonexistent")


def test_load_invalid_json_raises(tmp_path):
    review_dir = tmp_path / "bad-review"
    review_dir.mkdir()
    (review_dir / "state.json").write_text("not valid json")
    with pytest.raises(Exception):
        load(review_dir)


# --- scan ---

def test_scan_empty_dir(tmp_path):
    assert scan(tmp_path) == []


def test_scan_missing_dir(tmp_path):
    assert scan(tmp_path / "nonexistent") == []


def test_scan_finds_in_progress(tmp_path):
    review = _make_review("my-review-2026")
    save(review, tmp_path / "my-review-2026")

    results = scan(tmp_path)
    assert len(results) == 1
    assert results[0]["review_id"] == "my-review-2026"
    assert results[0]["status"] == ReviewStatus.pico_formulated.value


def test_scan_skips_complete(tmp_path):
    review = _make_review("done-review")
    review.status = ReviewStatus.complete
    save(review, tmp_path / "done-review")
    assert scan(tmp_path) == []


def test_scan_multiple_reviews(tmp_path):
    for slug in ["review-a", "review-b", "review-c"]:
        save(_make_review(slug), tmp_path / slug)
    results = scan(tmp_path)
    assert len(results) == 3
    ids = {r["review_id"] for r in results}
    assert ids == {"review-a", "review-b", "review-c"}


def test_scan_skips_dirs_without_state(tmp_path):
    (tmp_path / "empty-dir").mkdir()
    save(_make_review("real-review"), tmp_path / "real-review")
    results = scan(tmp_path)
    assert len(results) == 1


# --- artifact/status desync guard (B) ---

def test_scan_flags_artifact_status_desync(tmp_path):
    """A pre-complete review with terminal output artifacts on disk (e.g.
    executive_summary.md) has drifted — scan() must flag the desync."""
    review = _make_review("desynced-review")
    review.status = ReviewStatus.fulltext_assessed
    review_dir = tmp_path / "desynced-review"
    save(review, review_dir)
    (review_dir / "executive_summary.md").write_text("# Executive Summary\n")

    results = scan(tmp_path)
    assert len(results) == 1
    assert "artifact_desync" in results[0]
    assert "executive_summary.md" in results[0]["artifact_desync"]
    assert "fulltext_assessed" in results[0]["artifact_desync"]


def test_scan_no_desync_flag_for_clean_in_progress(tmp_path):
    """A normal in-progress review with no terminal artifacts is not flagged."""
    save(_make_review("clean-review"), tmp_path / "clean-review")
    results = scan(tmp_path)
    assert len(results) == 1
    assert "artifact_desync" not in results[0]


# --- path traversal protection ---

def test_safe_review_dir_blocks_traversal(tmp_path):
    reviews_root = tmp_path / "reviews"
    reviews_root.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        _safe_review_dir(reviews_root, "../outside")


def test_safe_review_dir_allows_valid_slug(tmp_path):
    reviews_root = tmp_path / "reviews"
    reviews_root.mkdir()
    result = _safe_review_dir(reviews_root, "chf-readmission-2026-04")
    assert result == (reviews_root / "chf-readmission-2026-04").resolve()
