"""Batch C monotonic-status tests: pico_refiner and review_type_classifier --save
must not REGRESS review.status when the review is already past that stage.

Both stages set review.status BACKWARD (pico_formulated / review_type_classified)
unconditionally. If --save is re-invoked out of order (e.g. an erroneous manual
re-run at a late status), the pipeline pointer silently regresses, orphaning
completed downstream work. These tests load a review at a LATE status, call
save() with valid input, and assert the fields are still updated but the status
is NOT regressed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agent import state as state_manager
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills import pico_refiner, review_type_classifier


# ---------------------------------------------------------------------------
# Helpers (mirror tests/test_skills_phase_c1.py fixtures)
# ---------------------------------------------------------------------------

def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-review-2026-04"
    review_dir.mkdir()
    review = Review(
        review_id="test-review-2026-04",
        raw_question="Does early mobility reduce hospital LOS in ICU patients?",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        **kwargs,
    )
    state_manager.save(review, review_dir)
    return review, review_dir


PICO_JSON = {
    "pico": {
        "P": "Adult ICU patients",
        "I": "Early mobility",
        "C": "Standard care",
        "O": ["Hospital LOS", "ICU LOS"],
        "S": None,
    },
    "inclusion_criteria": ["Adults ≥18"],
    "exclusion_criteria": ["Paediatric"],
    "fundamental_four": {
        "what_we_know": "Immobility causes deconditioning.",
        "what_we_dont_know": "Optimal timing.",
        "what_we_should_know": "Effect on LOS.",
        "why_we_should_know": "Reduce costs.",
    },
}


# ---------------------------------------------------------------------------
# review_type_classifier.save must not regress a late status
# ---------------------------------------------------------------------------

class TestReviewTypeClassifierMonotonic:
    def test_save_does_not_regress_late_status(self, tmp_path, capsys):
        # Review is already deep in the pipeline.
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        review_type_classifier.save(
            {"review_type": "scoping", "rationale": "Exploratory."}, review_dir
        )
        review = state_manager.load(review_dir)
        # Field IS updated ...
        assert review.review_type == ReviewType.scoping
        # ... but status is NOT regressed to review_type_classified.
        assert review.status == ReviewStatus.synthesized

    def test_save_still_sets_status_from_new(self, tmp_path, capsys):
        # Forward-only flow is unchanged: a brand-new review advances normally.
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.new)
        review_type_classifier.save(
            {"review_type": "systematic", "rationale": "Rigorous."}, review_dir
        )
        review = state_manager.load(review_dir)
        assert review.review_type == ReviewType.systematic
        assert review.status == ReviewStatus.review_type_classified


# ---------------------------------------------------------------------------
# pico_refiner.save must not regress a late status
# ---------------------------------------------------------------------------

class TestPicoRefinerMonotonic:
    def test_save_does_not_regress_late_status(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        pico_refiner.save(PICO_JSON, review_dir)
        review = state_manager.load(review_dir)
        # PICO fields ARE updated ...
        assert review.pico is not None
        assert review.pico.P == "Adult ICU patients"
        # ... but status is NOT regressed to pico_formulated.
        assert review.status == ReviewStatus.synthesized

    def test_save_still_sets_status_from_classified(self, tmp_path, capsys):
        # Forward-only flow is unchanged.
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.review_type_classified)
        pico_refiner.save(PICO_JSON, review_dir)
        review = state_manager.load(review_dir)
        assert review.pico is not None
        assert review.status == ReviewStatus.pico_formulated
