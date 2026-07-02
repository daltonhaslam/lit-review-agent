"""Issue #56: quality_appraiser save() must surface low_quality_count + score
distribution stats so Stage 13 skill warning can fire deterministically."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.review import Review, ReviewStatus, ReviewType
from src.skills.quality_appraiser import save as qa_save


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


def test_save_summary_includes_score_distribution(tmp_path, capsys):
    articles = [
        Article(pmid=str(i), title=f"A{i}", study_design="rct",
                screening_decision=ScreeningDecision.include)
        for i in range(5)
    ]
    rd = _make_review(tmp_path, articles)
    payload = {
        "appraisals": [
            {"orig_idx": 0, "score": 0.30, "rationale": "r"},
            {"orig_idx": 1, "score": 0.45, "rationale": "r"},
            {"orig_idx": 2, "score": 0.55, "rationale": "r"},
            {"orig_idx": 3, "score": 0.70, "rationale": "r"},
            {"orig_idx": 4, "score": 0.80, "rationale": "r"},
        ]
    }
    qa_save(rd, json.dumps(payload), batch_size=10, batch_offset=0)
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert summary["low_quality_count"] == 2  # 0.30, 0.45
    assert summary["mean_quality"] == pytest.approx(0.56, abs=0.01)
    assert summary["median_quality"] == pytest.approx(0.55, abs=0.01)
    assert summary["min_quality"] == pytest.approx(0.30)
    assert summary["max_quality"] == pytest.approx(0.80)
    assert "by_instrument_stats" in summary
    rob2 = summary["by_instrument_stats"].get("RoB 2.0")  # detect_instrument("rct") → "RoB 2.0"
    assert rob2 is not None
    assert rob2["count"] == 5
    assert rob2["median"] == pytest.approx(0.55, abs=0.01)


def test_save_summary_handles_zero_appraisals(tmp_path, capsys):
    """Edge case: no appraisals (e.g., all skipped). Stats keys must be present
    with safe defaults (no KeyError, no division by zero)."""
    articles = [Article(pmid="1", title="A", study_design="rct",
                       screening_decision=ScreeningDecision.include)]
    rd = _make_review(tmp_path, articles)
    qa_save(rd, json.dumps({"appraisals": []}), batch_size=10, batch_offset=0)
    summary = json.loads(capsys.readouterr().out)
    assert summary["low_quality_count"] == 0
    assert summary["mean_quality"] == 0.0
    assert summary["median_quality"] == 0.0
    assert summary["min_quality"] == 0.0
    assert summary["max_quality"] == 0.0
