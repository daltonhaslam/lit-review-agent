"""Batch C — Stage 8.5 citation_chaser.save() defensive-decision handling.

Malformed decision entries (missing "index", missing/null "decision") must NOT
crash save(). Every other slice-mode save handler uses defensive access
(cf. abstract_screener.save, which uses d.get(...) + isinstance guards). A crash
here leaves review status at abstract_screening and forces the already-completed
network chase to be re-screened.

Note: a candidate ABSENT from the decisions map defaults to `borderline` by design
(Issue #31) — an un-excluded citation-chased candidate still gets a full-text look
plus a human decision at Checkpoint 5 rather than being silently dropped. See
test_skills_phase_c4.py::test_default_borderline_decision_added_to_screened. So
these tests only assert include counts, which are unaffected by that default.

Reuses the fixture helpers from the parallel-screening test module.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.skills.citation_chaser import save as cc_save

from tests.test_skills_citation_chaser_parallel import (
    _make_article,
    _make_review,
    _write_candidates,
)

_NO_METADATA_STATS = {"total": 0}


@pytest.fixture(autouse=True)
def _patch_metadata_enrichment():
    """save() calls _enrich_missing_metadata via asyncio.run; patch it so these
    tests make no network calls (mirrors TestMergedSave)."""
    with patch(
        "src.skills.citation_chaser._enrich_missing_metadata",
        new=AsyncMock(return_value=_NO_METADATA_STATS),
    ):
        yield


def _capture_save(capsys, review_dir, decisions) -> dict:
    cc_save(review_dir, json.dumps({"decisions": decisions}))
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# Bug 1 — malformed decision entries must not crash save()
# ---------------------------------------------------------------------------

class TestMalformedDecisionsDoNotCrash:
    def test_missing_index_key_is_skipped(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(3)])

        decisions = [
            {"index": 0, "decision": "include"},
            {"decision": "include"},  # malformed: no "index"
            {"index": 2, "decision": "include"},
        ]
        # Must NOT raise KeyError.
        result = _capture_save(capsys, review_dir, decisions)
        assert result["new_includes"] == 2

    def test_null_decision_is_skipped(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(3)])

        decisions = [
            {"index": 0, "decision": "include"},
            {"index": 1, "decision": None},  # malformed: null decision -> .lower() AttributeError
            {"index": 2, "decision": "include"},
        ]
        result = _capture_save(capsys, review_dir, decisions)
        assert result["new_includes"] == 2

    def test_missing_decision_key_is_skipped(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_candidates(review_dir, [_make_article(i) for i in range(2)])

        decisions = [
            {"index": 0, "decision": "include"},
            {"index": 1},  # malformed: no "decision" key -> KeyError
        ]
        result = _capture_save(capsys, review_dir, decisions)
        assert result["new_includes"] == 1
