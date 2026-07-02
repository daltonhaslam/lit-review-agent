"""Batch-C regression tests for src.skills.gap_analyzer.

Covers a null (present-but-None) ``recommendation`` field arriving from the LLM.
``dict.get(key, default)`` returns None (NOT the default) when the key is present
but its value is null, so ``rec.upper()`` in _write_gap_analysis crashed with
AttributeError before any output was written.

Fixtures mirror tests/test_skills_phase_c4.py (kept local to avoid editing the
shared test file while sibling agents are mid-edit).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent import state as state_manager
from src.models.article import Article
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType
from src.skills.gap_analyzer import save as ga_save


def _make_pico() -> PICO:
    return PICO(
        P="Adult patients hospitalized with heart failure",
        I="Care transitions interventions",
        C="Usual care",
        O=["30-day all-cause readmission rate", "all-cause mortality"],
        inclusion_criteria=["Adults ≥18", "Primary diagnosis of heart failure"],
        exclusion_criteria=["Pediatric population"],
    )


def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-chf-2026-04"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="test-chf-2026-04",
        raw_question="Do care transitions reduce CHF readmission?",
        review_type=ReviewType.systematic,
        pico=_make_pico(),
        queries=QuerySet(
            pubmed=[Query(step_number="#1", query_string='"Heart Failure"[Mesh]', database="pubmed")],
        ),
    )
    defaults.update(kwargs)
    review = Review(**defaults)
    state_manager.save(review, review_dir)
    return review, review_dir


def _make_article(**kwargs) -> Article:
    defaults = dict(
        title="A study on heart failure readmission",
        authors=["Smith J", "Jones A"],
        year=2022,
        journal="NEJM",
        source_dbs=["pubmed"],
    )
    defaults.update(kwargs)
    return Article(**defaults)


def _write_included(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_included.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _gap_data(**kwargs) -> dict:
    base = {
        "pico_gaps": [],
        "outcome_gaps": [],
        "population_gaps": [],
        "recency_gaps": None,
        "design_gaps": [],
        "suggested_queries": [],
        "recommendation": "stop",
        "recommendation_rationale": "Adequate coverage.",
    }
    base.update(kwargs)
    return base


def _setup(tmp_path) -> Path:
    _, review_dir = _make_review(tmp_path, status=ReviewStatus.evidence_classified)
    _write_included(review_dir, [_make_article()])
    return review_dir


class TestGapAnalyzerNullRecommendation:
    def test_null_recommendation_does_not_crash(self, tmp_path, capsys):
        # LLM emitted `"recommendation": null` — .get(key, "stop") returns None,
        # not the default, so rec.upper() previously raised AttributeError.
        review_dir = _setup(tmp_path)
        ga_save(review_dir, json.dumps(_gap_data(recommendation=None)))
        # save() must complete: json + md written and status advanced.
        assert (review_dir / "gap_analysis.md").exists()
        assert (review_dir / "gap_analysis.json").exists()
        assert state_manager.load(review_dir).status == ReviewStatus.gap_analyzed

    def test_null_recommendation_renders_stop_in_md(self, tmp_path, capsys):
        review_dir = _setup(tmp_path)
        ga_save(review_dir, json.dumps(_gap_data(recommendation=None)))
        md = (review_dir / "gap_analysis.md").read_text()
        assert "**STOP**" in md

    def test_missing_recommendation_key_still_defaults_to_stop(self, tmp_path, capsys):
        review_dir = _setup(tmp_path)
        data = _gap_data()
        del data["recommendation"]
        ga_save(review_dir, json.dumps(data))
        md = (review_dir / "gap_analysis.md").read_text()
        assert "**STOP**" in md
