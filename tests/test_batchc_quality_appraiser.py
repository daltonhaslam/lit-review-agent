"""Batch C — quality_appraiser bug-group.

(1) Instrument-routing bug: detect_instrument normalizes underscores to spaces
    then tests _RCT_KEYWORDS ("rct" / "randomized") BEFORE any non-RCT guard, so
    NHMRC Level III-1 non-randomized designs (quasi_rct, non_randomized_trial)
    wrongly get the RCT-only RoB 2.0 tool instead of STROBE.

(2) Notes-wipe: _apply_appraisal unconditionally sets quality_notes to
    notes/confidence-or-"", and save() stamps every in-range index even when the
    payload lacks that index — wiping a previously-set quality_notes on an
    empty/partial payload (reachable via the /lit-review-update re-appraisal path).
"""
from __future__ import annotations

import json
from pathlib import Path

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.review import Review, ReviewStatus, ReviewType
from src.skills.quality_appraiser import (
    _apply_appraisal,
    detect_instrument,
    save as qa_save,
)


# ===========================================================================
# (1) Instrument-routing guard: non-randomized designs must NOT get RoB 2.0
# ===========================================================================

class TestDetectInstrumentNonRCTGuard:
    def test_quasi_rct_gets_strobe(self):
        # "quasi rct" contains "rct" but is a Level III-1 non-randomized design.
        assert detect_instrument("quasi_rct") == "STROBE"

    def test_non_randomized_trial_gets_strobe(self):
        # "non randomized trial" contains "randomized" but is not an RCT.
        assert detect_instrument("non_randomized_trial") == "STROBE"

    def test_rct_still_gets_rob2(self):
        assert detect_instrument("rct") == "RoB 2.0"

    def test_randomized_controlled_trial_still_gets_rob2(self):
        assert detect_instrument("Randomized controlled trial") == "RoB 2.0"

    def test_controlled_before_after_stays_strobe(self):
        assert detect_instrument("controlled_before_after") == "STROBE"

    def test_interrupted_time_series_stays_strobe(self):
        assert detect_instrument("interrupted_time_series") == "STROBE"


# ===========================================================================
# (2) Notes-wipe: empty/partial payload must not clobber existing notes
# ===========================================================================

def _make_review(tmp_path: Path, articles: list[Article]) -> Path:
    rd = tmp_path / "rv"
    rd.mkdir()
    review = Review(
        review_id="rv",
        raw_question="q",
        review_type=ReviewType.systematic,
        status=ReviewStatus.enriched,
        pico=PICO(P="x", I="y", C="z", O=["o"]),
        articles_included_file="articles_included.json",
    )
    state_manager.save(review, rd)
    (rd / "articles_included.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )
    return rd


class TestApplyAppraisalPreservesNotes:
    def test_empty_payload_preserves_existing_notes(self):
        a = Article(pmid="1", title="A", study_design="rct",
                    screening_decision=ScreeningDecision.include)
        a.quality_notes = "Prior appraisal: moderate quality"
        _apply_appraisal(a, "RoB 2.0", {})
        assert a.quality_notes == "Prior appraisal: moderate quality"

    def test_new_notes_overwrite_existing(self):
        a = Article(pmid="1", title="A", study_design="rct",
                    screening_decision=ScreeningDecision.include)
        a.quality_notes = "old"
        _apply_appraisal(a, "RoB 2.0", {"score": 0.5, "notes": "new"})
        assert a.quality_notes == "new"


def test_save_empty_payload_preserves_prior_notes(tmp_path, capsys):
    """/lit-review-update serial re-appraisal path: a batch payload that omits an
    already-noted article must not wipe its quality_notes back to ''."""
    a = Article(pmid="1", title="A", study_design="rct",
                screening_decision=ScreeningDecision.include)
    a.quality_notes = "Prior appraisal notes"
    a.quality_score = 0.7
    rd = _make_review(tmp_path, [a])

    qa_save(rd, json.dumps({"appraisals": []}), batch_size=10, batch_offset=0)
    capsys.readouterr()

    saved = json.loads((rd / "articles_included.json").read_text())
    assert saved[0]["quality_notes"] == "Prior appraisal notes"
