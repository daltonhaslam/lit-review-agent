# tests/test_skills_evidence_classifier_persistence.py
"""Issue #57 verification: surrogate_outcome_flag must persist on
articles_included.json per-article (not just as an aggregate count).

Code path under test:
  src/skills/evidence_classifier.py:149 — article.surrogate_outcome_flag = result
  src/skills/evidence_classifier.py:154 — included_path.write_text(...)
"""
from __future__ import annotations

import json
from pathlib import Path

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.review import Review, ReviewStatus, ReviewType
from src.skills.evidence_classifier import classify


def _make_review(tmp_path: Path) -> Path:
    review_dir = tmp_path / "rv"
    review_dir.mkdir()
    review = Review(
        review_id="rv",
        raw_question="q",
        review_type=ReviewType.systematic,
        # classify() requires status == quality_appraised (evidence_classifier.py:133)
        status=ReviewStatus.quality_appraised,
        pico=PICO(P="adults", I="x", C="usual care", O=["mortality"]),
        articles_included_file="articles_included.json",
    )
    state_manager.save(review, review_dir)
    articles = [
        Article(
            pmid="1",
            title="A",
            study_design="rct",
            primary_outcomes=["LDL cholesterol"],  # surrogate vs. mortality outcome
            screening_decision=ScreeningDecision.include,
        ),
        Article(
            pmid="2",
            title="B",
            study_design="rct",
            primary_outcomes=["mortality"],  # not surrogate
            screening_decision=ScreeningDecision.include,
        ),
    ]
    (review_dir / "articles_included.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )
    return review_dir


def test_surrogate_outcome_flag_persists_per_article(tmp_path):
    review_dir = _make_review(tmp_path)
    summary = classify(review_dir)

    persisted = json.loads((review_dir / "articles_included.json").read_text())
    flags = {a["pmid"]: a.get("surrogate_outcome_flag") for a in persisted}

    assert summary["surrogate_flagged"] == 1
    assert flags["1"] is True, "LDL cholesterol vs. mortality must be flagged surrogate"
    assert flags["2"] is False, "mortality vs. mortality must not be flagged surrogate"
