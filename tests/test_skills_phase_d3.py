"""Unit tests for Phase D3 — PRISMA completeness + PRISMA-trAIce.

Tests:
  TestSearchLogGeneration (4)  — search_log.md written with correct structure at retrieval time
  TestHitCountStorage (3)      — query.hits populated from by_source counts after retrieve()
  TestAIDisclosureGeneration (4) — ai_disclosure.md written with required PRISMA-trAIce sections
  TestSafeReviewDir (2)        — _safe_review_dir rejects path traversal slugs

No LLM or live API calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import state as state_manager
from src.agent.state import _safe_review_dir
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.multi_source_retriever import _write_hits_to_queries, _write_search_log
from src.skills.output_formatter import _write_ai_disclosure, _EXPECTED_ARTIFACTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "hf-d3-2026"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="hf-d3-2026",
        raw_question="Do care transitions reduce HF readmission?",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        status=ReviewStatus.queries_built,
        pico=PICO(
            P="Heart failure patients",
            I="Care transitions",
            C="Usual care",
            O=["30-day readmission"],
        ),
        queries=QuerySet(
            pubmed=[
                Query(step_number="#1", query_string='"Heart Failure"[Mesh]', database="pubmed"),
                Query(step_number="#2", query_string="(#1) AND English[lang]", database="pubmed"),
            ],
            openalex=[Query(step_number="#1", query_string="heart failure readmission", database="openalex")],
            semantic_scholar=[Query(step_number="#1", query_string="heart failure readmission", database="semantic_scholar")],
        ),
        sources_searched=["pubmed", "openalex", "semantic_scholar"],
    )
    defaults.update(kwargs)
    review = Review(**defaults)
    state_manager.save(review, review_dir)
    return review, review_dir


def _make_article(**kwargs) -> Article:
    defaults = dict(
        title="A CHF study",
        authors=["Smith J"],
        year=2022,
        journal="NEJM",
        source_dbs=["pubmed"],
        screening_decision=ScreeningDecision.include,
    )
    defaults.update(kwargs)
    return Article(**defaults)


# ===========================================================================
# TestSearchLogGeneration
# ===========================================================================

class TestSearchLogGeneration:
    def test_search_log_created(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        by_source = {"pubmed": 12, "openalex": 8, "semantic_scholar": 5}
        _write_search_log(review, review_dir, by_source)
        assert (review_dir / "search_log.md").exists()

    def test_search_log_contains_database_names(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        by_source = {"pubmed": 12, "openalex": 8, "semantic_scholar": 5}
        _write_search_log(review, review_dir, by_source)
        content = (review_dir / "search_log.md").read_text()
        assert "PubMed" in content
        assert "OpenAlex" in content
        assert "Semantic Scholar" in content

    def test_search_log_contains_hit_counts(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        by_source = {"pubmed": 42, "openalex": 17}
        _write_search_log(review, review_dir, by_source)
        content = (review_dir / "search_log.md").read_text()
        assert "42" in content
        assert "17" in content

    def test_search_log_contains_query_string(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        by_source = {"pubmed": 10}
        _write_search_log(review, review_dir, by_source)
        content = (review_dir / "search_log.md").read_text()
        assert '"Heart Failure"[Mesh]' in content


# ===========================================================================
# TestHitCountStorage
# ===========================================================================

class TestHitCountStorage:
    def test_pubmed_query_hits_written(self, tmp_path):
        review, _ = _make_review(tmp_path)
        by_source = {"pubmed": 55, "openalex": 20, "semantic_scholar": 10}
        _write_hits_to_queries(review, by_source)
        assert review.queries.pubmed[-1].hits == 55

    def test_openalex_query_hits_written(self, tmp_path):
        review, _ = _make_review(tmp_path)
        by_source = {"pubmed": 0, "openalex": 30, "semantic_scholar": 0}
        _write_hits_to_queries(review, by_source)
        assert review.queries.openalex[-1].hits == 30

    def test_hits_zero_when_source_returns_empty(self, tmp_path):
        review, _ = _make_review(tmp_path)
        by_source = {"pubmed": 0, "openalex": 0, "semantic_scholar": 0}
        _write_hits_to_queries(review, by_source)
        assert review.queries.pubmed[-1].hits == 0
        assert review.queries.openalex[-1].hits == 0


# ===========================================================================
# TestAIDisclosureGeneration
# ===========================================================================

class TestAIDisclosureGeneration:
    def test_ai_disclosure_file_written(self, tmp_path):
        review, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_ai_disclosure(review, review_dir)
        assert (review_dir / "ai_disclosure.md").exists()

    def test_ai_disclosure_contains_ai_stages_section(self, tmp_path):
        review, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_ai_disclosure(review, review_dir)
        content = (review_dir / "ai_disclosure.md").read_text()
        assert "AI-Assisted Stages" in content

    def test_ai_disclosure_contains_human_review_section(self, tmp_path):
        review, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_ai_disclosure(review, review_dir)
        content = (review_dir / "ai_disclosure.md").read_text()
        assert "Human Review Points" in content

    def test_ai_disclosure_in_expected_artifacts(self):
        assert "ai_disclosure.md" in _EXPECTED_ARTIFACTS


# ===========================================================================
# TestSafeReviewDir
# ===========================================================================

class TestSafeReviewDir:
    def test_valid_slug_returns_child_path(self, tmp_path):
        reviews_root = tmp_path / "reviews"
        reviews_root.mkdir()
        result = _safe_review_dir(reviews_root, "my-review-2026")
        assert result.parent.resolve() == reviews_root.resolve()

    def test_traversal_slug_raises(self, tmp_path):
        reviews_root = tmp_path / "reviews"
        reviews_root.mkdir()
        with pytest.raises(ValueError, match="escapes"):
            _safe_review_dir(reviews_root, "../outside")
