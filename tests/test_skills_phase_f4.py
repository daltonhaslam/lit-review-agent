"""Unit tests for Phase F4 — screener + retriever robustness.

Covers Issues #22b, #24, #25, #28:
  #22b  zero-result WARNING in multi_source_retriever
  #24   auto-detect resume offset in abstract_screener --prepare
  #25   date-range pre-filter in abstract_screener --prepare
  #28   write prisma_counts.eligibility on screening completion
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import DateRange, Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.abstract_screener import prepare, save


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Pediatric patients",
        I="Observation admission",
        C="Inpatient admission",
        O=["LOS", "readmission"],
        inclusion_criteria=["Age < 18"],
        exclusion_criteria=["Adults"],
    )
    defaults.update(kwargs)
    return PICO(**defaults)


def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-review"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="test-review",
        raw_question="Test question",
        review_type=ReviewType.scoping,
        screening_mode=ScreeningMode.thorough,
        status=ReviewStatus.deduplicated,
        pico=_make_pico(),
        queries=QuerySet(
            pubmed=[Query(step_number="#1", query_string='"Pediatrics"[Mesh]', database="pubmed")],
        ),
    )
    defaults.update(kwargs)
    review = Review(**defaults)
    state_manager.save(review, review_dir)
    return review, review_dir


def _make_article(title: str = "A study", year: int = 2022, **kwargs) -> Article:
    defaults = dict(title=title, year=year, authors=["Smith J"], journal="NEJM", source_dbs=["pubmed"])
    defaults.update(kwargs)
    return Article(**defaults)


def _write_deduped(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_deduped.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _write_screened(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_screened.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=0) -> dict:
    prepare(review_dir, batch_size, batch_offset)
    return json.loads(capsys.readouterr().out)


def _decisions_json(decisions: list[dict]) -> str:
    return json.dumps({"decisions": decisions})


def _capture_save(capsys, review_dir, offset, decisions) -> dict:
    save(review_dir, offset, _decisions_json(decisions))
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# Issue #24 — auto-detect resume offset
# ---------------------------------------------------------------------------

class TestAutoResumeOffset:
    def test_no_screened_file_auto_resume_zero(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(title=f"Article {i}") for i in range(40)]
        _write_deduped(review_dir, articles)

        out = _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=0)

        assert out["auto_resume_offset"] == 0
        assert out["effective_offset"] == 0
        assert out["articles"][0]["article_index"] == 0

    def test_fifty_decided_starts_at_fifty(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(title=f"Article {i}") for i in range(100)]
        decided = [
            Article(**{**a.model_dump(mode="json"), "screening_decision": ScreeningDecision.exclude})
            for a in articles[:50]
        ]
        _write_screened(review_dir, decided + articles[50:])
        _write_deduped(review_dir, articles)

        out = _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=0)

        assert out["auto_resume_offset"] == 50
        assert out["effective_offset"] == 50
        assert out["articles"][0]["article_index"] == 50

    def test_explicit_offset_greater_than_decided_wins(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(title=f"Article {i}") for i in range(100)]
        decided = [
            Article(**{**a.model_dump(mode="json"), "screening_decision": ScreeningDecision.exclude})
            for a in articles[:30]
        ]
        _write_screened(review_dir, decided + articles[30:])
        _write_deduped(review_dir, articles)

        out = _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=60)

        assert out["auto_resume_offset"] == 30
        assert out["effective_offset"] == 60
        assert out["articles"][0]["article_index"] == 60

    def test_decided_count_greater_than_explicit_offset_uses_decided(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(title=f"Article {i}") for i in range(100)]
        decided = [
            Article(**{**a.model_dump(mode="json"), "screening_decision": ScreeningDecision.exclude})
            for a in articles[:50]
        ]
        _write_screened(review_dir, decided + articles[50:])
        _write_deduped(review_dir, articles)

        # batch_offset=20 but 50 are decided → effective_offset=50
        out = _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=20)

        assert out["effective_offset"] == 50


# ---------------------------------------------------------------------------
# Issue #25 — date-range pre-filter
# ---------------------------------------------------------------------------

class TestDatePrefilter:
    def test_no_date_range_no_prefilter(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(title=f"Article {i}", year=y) for i, y in enumerate([2005, 2010, 2025])]
        _write_deduped(review_dir, articles)

        out = _capture_prepare(capsys, review_dir)

        assert out["pre_filtered_this_call"] == 0
        assert out["pre_filtered_total"] == 0
        assert out["pending_articles"] == 3

    def test_date_range_excludes_out_of_range_articles(self, tmp_path, capsys):
        review, review_dir = _make_review(tmp_path, date_range=DateRange(start=2010, end=2024))
        # 5 articles: 2 before range (idx 0,1), 1 in range (idx 2), 2 after range (idx 3,4)
        articles = [
            _make_article(title="Pre-range 1", year=2005),
            _make_article(title="Pre-range 2", year=2009),
            _make_article(title="In range", year=2015),
            _make_article(title="Post-range 1", year=2025),
            _make_article(title="Post-range 2", year=2026),
        ]
        _write_deduped(review_dir, articles)

        out = _capture_prepare(capsys, review_dir)

        assert out["pre_filtered_this_call"] == 4
        assert out["pre_filtered_total"] == 4
        assert out["pending_articles"] == 1
        assert len(out["articles"]) == 1
        assert out["articles"][0]["title"] == "In range"
        assert out["articles"][0]["article_index"] == 2   # global index preserved
        assert out["effective_offset"] == 2               # first pending article's index

    def test_prefilter_writes_screened_file(self, tmp_path):
        review, review_dir = _make_review(tmp_path, date_range=DateRange(start=2015, end=2020))
        articles = [
            _make_article(title="Old", year=2000),
            _make_article(title="New", year=2018),
        ]
        _write_deduped(review_dir, articles)

        import sys
        from io import StringIO
        old_stdout, sys.stdout = sys.stdout, StringIO()
        try:
            prepare(review_dir, 20, 0)
        finally:
            sys.stdout = old_stdout

        screened_path = review_dir / "articles_screened.json"
        assert screened_path.exists()
        screened = json.loads(screened_path.read_text())
        old_article = next(a for a in screened if a["title"] == "Old")
        assert old_article["screening_decision"] == "exclude"
        assert old_article["screening_reason_category"] == "date_out_of_range"

    def test_prefilter_idempotent_on_second_call(self, tmp_path, capsys):
        review, review_dir = _make_review(tmp_path, date_range=DateRange(start=2015, end=2020))
        # 3 articles: 1 out-of-range, 2 in-range; screen only the first in-range, leave second pending
        articles = [
            _make_article(title="Old", year=2000),
            _make_article(title="In range A", year=2018),
            _make_article(title="In range B", year=2019),
        ]
        _write_deduped(review_dir, articles)

        # First call: filters 1 article, returns the 2 in-range articles
        out1 = _capture_prepare(capsys, review_dir)
        assert out1["pre_filtered_this_call"] == 1

        # Screen only the first in-range article; second remains pending
        save(review_dir, 0, _decisions_json([
            {"article_index": out1["articles"][0]["article_index"],
             "decision": "include", "reason": "relevant", "reason_category": None}
        ]))
        capsys.readouterr()

        # Restore to deduplicated status so prepare() runs again
        loaded = state_manager.load(review_dir)
        loaded.status = ReviewStatus.deduplicated
        state_manager.save(loaded, review_dir)

        # Second call: no new articles to filter (idempotent)
        out2 = _capture_prepare(capsys, review_dir)
        assert out2["pre_filtered_this_call"] == 0
        assert out2["pre_filtered_total"] == 1
        assert out2["pending_articles"] == 1

    def test_articles_without_year_not_prefiltered(self, tmp_path, capsys):
        review, review_dir = _make_review(tmp_path, date_range=DateRange(start=2015, end=2020))
        articles = [
            _make_article(title="No year", year=None),
            _make_article(title="In range", year=2018),
        ]
        _write_deduped(review_dir, articles)

        out = _capture_prepare(capsys, review_dir)

        # Article without year should NOT be excluded by prefilter
        assert out["pre_filtered_this_call"] == 0
        assert out["pending_articles"] == 2


# ---------------------------------------------------------------------------
# Issue #28 — write eligibility to PRISMA on screening completion
# ---------------------------------------------------------------------------

class TestEligibilityOnCompletion:
    def test_eligibility_written_on_final_batch(self, tmp_path, capsys):
        review, review_dir = _make_review(tmp_path)
        articles = [_make_article(title=f"Article {i}") for i in range(3)]
        _write_deduped(review_dir, articles)

        # Single batch covers all 3 articles
        _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        _capture_save(capsys, review_dir, 0, [
            {"article_index": 0, "decision": "include", "reason": "relevant", "reason_category": None},
            {"article_index": 1, "decision": "borderline", "reason": "maybe", "reason_category": None},
            {"article_index": 2, "decision": "exclude", "reason": "off-topic", "reason_category": "wrong_population"},
        ])

        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.eligibility == 2  # 1 include + 1 borderline

    def test_eligibility_include_only(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(title=f"Art {i}") for i in range(2)]
        _write_deduped(review_dir, articles)

        _capture_prepare(capsys, review_dir)
        _capture_save(capsys, review_dir, 0, [
            {"article_index": 0, "decision": "include", "reason": "ok", "reason_category": None},
            {"article_index": 1, "decision": "exclude", "reason": "no", "reason_category": "wrong_outcome"},
        ])

        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.eligibility == 1

    def test_eligibility_zero_when_all_excluded(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(title=f"Art {i}") for i in range(2)]
        _write_deduped(review_dir, articles)

        _capture_prepare(capsys, review_dir)
        _capture_save(capsys, review_dir, 0, [
            {"article_index": 0, "decision": "exclude", "reason": "no", "reason_category": "wrong_outcome"},
            {"article_index": 1, "decision": "exclude", "reason": "no", "reason_category": "wrong_population"},
        ])

        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.eligibility == 0

    def test_eligibility_in_save_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(title=f"Art {i}") for i in range(2)]
        _write_deduped(review_dir, articles)

        _capture_prepare(capsys, review_dir)
        out = _capture_save(capsys, review_dir, 0, [
            {"article_index": 0, "decision": "include", "reason": "ok", "reason_category": None},
            {"article_index": 1, "decision": "borderline", "reason": "maybe", "reason_category": None},
        ])

        # complete=True on final batch
        assert out["complete"] is True


# ---------------------------------------------------------------------------
# Issue #22b — zero-result WARNING in multi_source_retriever
# ---------------------------------------------------------------------------

class TestZeroResultWarning:
    @pytest.mark.asyncio
    async def test_zero_pubmed_results_emits_warning(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)

        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar", new=AsyncMock(return_value=[])),
        ):
            result = await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        assert "WARNING" in result["retrieval_errors"]["pubmed"]
        assert "pubmed" in result["retrieval_errors"]["pubmed"]

    @pytest.mark.asyncio
    async def test_nonzero_result_source_no_warning(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(pmid="1", title="Result")]

        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=articles)),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar", new=AsyncMock(return_value=[])),
        ):
            result = await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        assert "pubmed" not in result["retrieval_errors"]

    @pytest.mark.asyncio
    async def test_exception_not_overwritten_by_warning(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)

        with (
            patch(
                "src.skills.multi_source_retriever._search_pubmed",
                new=AsyncMock(side_effect=Exception("connection refused")),
            ),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar", new=AsyncMock(return_value=[])),
        ):
            result = await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        # Exception message, not WARNING
        assert "connection refused" in result["retrieval_errors"]["pubmed"]
        assert "WARNING" not in result["retrieval_errors"]["pubmed"]
