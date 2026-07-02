"""Batch C — data_extractor merged-save completion observability.

When a merged offset-0 save over a >batch_size set is missing one or more
orig_idx, full coverage is not achieved, the evidence table is not written,
and status stays fulltext_assessed. The summary must report WHICH orig_idx
are missing so the orchestrator can re-run the missing slice.

Fixture pattern mirrors tests/test_skills_phase_h_parallel_extract_appraise.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.data_extractor import (
    BATCH_SIZE_DEFAULT as DE_BATCH_DEFAULT,
    save as de_save,
)


# ---------------------------------------------------------------------------
# Helpers (mirrors test_skills_phase_h_parallel_extract_appraise.py)
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


def _capture_de_save(capsys, review_dir, extractions, **kwargs) -> dict:
    de_save(
        review_dir,
        json.dumps({"extractions": extractions}),
        kwargs.pop("batch_size", DE_BATCH_DEFAULT),
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


# ---------------------------------------------------------------------------
# Merged offset-0 save missing one orig_idx → reports missing indices
# ---------------------------------------------------------------------------

class TestMergedSaveReportsMissingIndices:
    def test_missing_one_idx_reports_incomplete_and_lists_missing(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(i) for i in range(60)])

        # Merged offset-0 save over 60 articles, one orig_idx dropped (idx 42).
        # batch_size=50 so positional rule (0+50>=60) is False; missing idx makes
        # full_coverage False → complete stays False, evidence table not written.
        missing_idx = 42
        merged = [_full_extraction(i) for i in range(60) if i != missing_idx]
        result = _capture_de_save(capsys, review_dir, merged, batch_size=50, offset=0)

        assert result["complete"] is False
        # Not-complete on the merged path must surface which orig_idx are missing
        # so the orchestrator can re-run the missing slice.
        assert "missing_orig_idx" in result
        assert result["missing_orig_idx"] == [missing_idx]

        # Semantics unchanged: no evidence table, status not advanced.
        assert not (review_dir / "evidence_table.csv").exists()
        assert state_manager.load(review_dir).status == ReviewStatus.fulltext_assessed

    def test_multiple_missing_idxs_reported_sorted(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(i) for i in range(60)])

        missing = {7, 42, 58}
        merged = [_full_extraction(i) for i in range(60) if i not in missing]
        result = _capture_de_save(capsys, review_dir, merged, batch_size=50, offset=0)

        assert result["complete"] is False
        assert result["missing_orig_idx"] == sorted(missing)

    def test_complete_save_omits_missing_key(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article(i) for i in range(60)])

        merged = [_full_extraction(i) for i in range(60)]
        result = _capture_de_save(capsys, review_dir, merged, batch_size=50, offset=0)

        assert result["complete"] is True
        assert "missing_orig_idx" not in result
