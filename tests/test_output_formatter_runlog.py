"""Tests for the flag-gated RUNLOG completion writer (#64-A)."""
from __future__ import annotations

import json
from pathlib import Path

from src.models.review import PRISMACounts, Review, ReviewStatus, ReviewType
from src.skills.output_formatter import (
    _default_runlog_path,
    _repo_root,
    _write_runlog_completion,
)

SKELETON = "# Run Log\n\n<!-- New entries go here -->\n"


def _review(**kw) -> Review:
    defaults = dict(
        review_id="demo-2026-07",
        raw_question="Does X reduce Y?",
        review_type=ReviewType.systematic,
        status=ReviewStatus.complete,
        iterations=0,
        prisma_counts=PRISMACounts(
            identified=100, screened=90, eligibility=20, included=12, citation_chasing_added=3
        ),
    )
    defaults.update(kw)
    return Review(**defaults)


class TestRunlogCompletion:
    def test_inserts_new_block_after_marker(self, tmp_path):
        p = tmp_path / "RUNLOG.md"
        p.write_text(SKELETON)
        _write_runlog_completion(_review(), p, present=["executive_summary.md"], total_included=12)
        text = p.read_text()
        assert "demo-2026-07 [COMPLETE]" in text
        # newest-first: the new block sits after the marker
        assert text.index("demo-2026-07 [COMPLETE]") > text.index("<!-- New entries go here -->")

    def test_replaces_in_progress_block_preserving_session_notes(self, tmp_path):
        p = tmp_path / "RUNLOG.md"
        p.write_text(
            SKELETON
            + "\n## demo-2026-07\n\n**Question:** Does X reduce Y?\n**Status:** IN PROGRESS\n\n"
            "### Session notes\n"
            "- [2026-06-30] Stage: screening — no issues\n"
            "- [2026-07-01] Stage: synthesized — no issues\n"
        )
        _write_runlog_completion(_review(), p, present=["executive_summary.md"], total_included=12)
        text = p.read_text()
        assert "[COMPLETE]" in text
        assert "IN PROGRESS" not in text
        assert "- [2026-06-30] Stage: screening — no issues" in text  # preserved
        assert "- [2026-07-01] Stage: synthesized — no issues" in text

    def test_idempotent_no_duplicate_blocks(self, tmp_path):
        p = tmp_path / "RUNLOG.md"
        p.write_text(SKELETON)
        _write_runlog_completion(_review(), p, present=[], total_included=12)
        _write_runlog_completion(_review(), p, present=[], total_included=12)
        assert p.read_text().count("[COMPLETE]") == 1

    def test_creates_skeleton_when_file_missing(self, tmp_path):
        p = tmp_path / "RUNLOG.md"  # does not exist yet
        _write_runlog_completion(_review(), p, present=[], total_included=12)
        text = p.read_text()
        assert "# Run Log" in text
        assert "[COMPLETE]" in text

    def test_total_included_in_prisma_line(self, tmp_path):
        p = tmp_path / "RUNLOG.md"
        p.write_text(SKELETON)
        _write_runlog_completion(_review(), p, present=[], total_included=57)
        assert "Included 57" in p.read_text()

    def test_missing_artifact_marked_unchecked(self, tmp_path):
        p = tmp_path / "RUNLOG.md"
        p.write_text(SKELETON)
        _write_runlog_completion(
            _review(), p, present=["executive_summary.md"], total_included=12
        )
        text = p.read_text()
        assert "executive_summary ✓" in text
        assert "gap_analysis ✗" in text

    def test_does_not_clobber_review_whose_id_is_a_superstring(self, tmp_path):
        # An anchored heading match must not treat `demo-2026-07-pilot` as this review's block
        # just because `demo-2026-07` is a substring — else finalizing one review silently
        # overwrites another's entry (session notes and all) in the shared append-only log.
        p = tmp_path / "RUNLOG.md"
        p.write_text(
            SKELETON
            + "\n## 2026-07-01 — demo-2026-07-pilot [COMPLETE]\n\n"
            "**Question:** Pilot?\n\n### Session notes\n- [2026-07-01] pilot note\n"
        )
        _write_runlog_completion(_review(), p, present=[], total_included=12)  # id demo-2026-07
        text = p.read_text()
        assert "demo-2026-07-pilot [COMPLETE]" in text  # superstring block survives
        assert "- [2026-07-01] pilot note" in text
        assert text.count("[COMPLETE]") == 2  # a distinct new block was added

    def test_default_runlog_path_is_repo_root(self):
        assert _default_runlog_path() == _repo_root() / "RUNLOG.md"
        assert _default_runlog_path().name == "RUNLOG.md"


class TestSaveRunlogWiring:
    def _setup(self, tmp_path):
        from src.agent import state as state_manager
        from src.models.pico import PICO

        review_dir = tmp_path / "demo-2026-07"
        review_dir.mkdir()
        review = _review(pico=PICO(P="p", I="i", C="c", O=["o"]), status=ReviewStatus.synthesized)
        state_manager.save(review, review_dir)
        (review_dir / "articles_included.json").write_text("[]")
        (review_dir / "synthesis.json").write_text(json.dumps({"total_included": 0, "narrative": {}}))
        return review_dir

    def test_save_without_flag_leaves_runlog_untouched(self, tmp_path):
        from src.skills.output_formatter import save

        review_dir = self._setup(tmp_path)
        runlog = tmp_path / "RUNLOG.md"
        runlog.write_text(SKELETON)
        before = runlog.read_text()
        save(review_dir, json.dumps({"executive_summary": "# S\n\nBody."}))
        assert runlog.read_text() == before  # byte-for-byte unchanged

    def test_save_with_flag_writes_completion_block(self, tmp_path):
        from src.skills.output_formatter import save

        review_dir = self._setup(tmp_path)
        runlog = tmp_path / "RUNLOG.md"
        runlog.write_text(SKELETON)
        save(review_dir, json.dumps({"executive_summary": "# S\n\nBody."}),
             runlog=True, runlog_path=runlog)
        text = runlog.read_text()
        assert "demo-2026-07 [COMPLETE]" in text
