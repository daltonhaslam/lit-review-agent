"""Unit tests for Phase 6 — Iteration + Polish.

Covers: iteration_manager (fingerprint, seen_ids),
seen_article_ids model field.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent import state as state_manager
from src.models.article import Article
from src.models.pico import PICO
from src.models.review import PRISMACounts, Review, ReviewStatus, ReviewType

from src.skills.iteration_manager import (
    _build_seen_ids,
    _fingerprint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Adult HF inpatients",
        I="Care transitions",
        C="Usual care",
        O=["30-day readmission"],
        inclusion_criteria=["Adults ≥18"],
        exclusion_criteria=["Pediatric"],
    )
    defaults.update(kwargs)
    return PICO(**defaults)


def _make_article(**kwargs) -> Article:
    defaults = dict(
        title="Transitional care reduces HF readmissions: a randomised trial",
        abstract="Background: care transitions reduce readmissions.",
        authors=["Smith J", "Jones K"],
        year=2022,
        journal="NEJM",
        doi="10.1056/nejmoa2022001",
        pmid="12345001",
        source_dbs=["pubmed"],
    )
    defaults.update(kwargs)
    return Article(**defaults)


def _make_review(tmp_path: Path, status=ReviewStatus.gap_analyzed, iterations=0, max_iterations=3) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-review"
    review_dir.mkdir()
    review = Review(
        review_id="test-review",
        raw_question="What care transitions reduce HF readmissions?",
        review_type=ReviewType.systematic,
        status=status,
        pico=_make_pico(),
        iterations=iterations,
        max_iterations=max_iterations,
        prisma_counts=PRISMACounts(identified=100, screened=80, eligibility=20, included=15),
    )
    state_manager.save(review, review_dir)
    return review, review_dir


# ---------------------------------------------------------------------------
# Review model: seen_article_ids field
# ---------------------------------------------------------------------------

class TestReviewModelSeenIds:
    def test_seen_article_ids_defaults_empty(self):
        r = Review(review_id="x", raw_question="q")
        assert r.seen_article_ids == []

    def test_seen_article_ids_persists(self, tmp_path):
        review_dir = tmp_path / "r"
        review_dir.mkdir()
        r = Review(review_id="r", raw_question="q", seen_article_ids=["doi:10.1/x", "pmid:999"])
        state_manager.save(r, review_dir)
        loaded = state_manager.load(review_dir)
        assert "doi:10.1/x" in loaded.seen_article_ids
        assert "pmid:999" in loaded.seen_article_ids


# ---------------------------------------------------------------------------
# _fingerprint
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_doi_preferred(self):
        a = _make_article(doi="10.1056/nejmoa2022001", pmid="111")
        assert _fingerprint(a) == "doi:10.1056/nejmoa2022001"

    def test_doi_strips_prefix(self):
        a = _make_article(doi="https://doi.org/10.1056/test", pmid=None)
        assert _fingerprint(a) == "doi:10.1056/test"

    def test_doi_lowercased(self):
        a = _make_article(doi="10.1056/UPPER", pmid=None)
        assert _fingerprint(a).startswith("doi:")
        assert _fingerprint(a) == "doi:10.1056/upper"

    def test_pmid_fallback_when_no_doi(self):
        a = _make_article(doi=None, pmid="99999")
        assert _fingerprint(a) == "pmid:99999"

    def test_title_fallback_when_no_doi_no_pmid(self):
        a = _make_article(doi=None, pmid=None, title="My Article Title")
        fp = _fingerprint(a)
        assert fp.startswith("title:")
        assert "myarticletitle" in fp

    def test_title_truncated_to_80(self):
        a = _make_article(doi=None, pmid=None, title="x" * 200)
        fp = _fingerprint(a)
        assert fp.startswith("title:")
        assert len(fp) <= len("title:") + 80


# ---------------------------------------------------------------------------
# _build_seen_ids
# ---------------------------------------------------------------------------

class TestBuildSeenIds:
    def test_loads_from_review_seen_article_ids(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        review.seen_article_ids = ["doi:10.1/a", "pmid:123"]
        state_manager.save(review, review_dir)
        seen = _build_seen_ids(review, review_dir)
        assert "doi:10.1/a" in seen
        assert "pmid:123" in seen

    def test_loads_from_screened_json(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        a = _make_article(doi="10.9999/screened", pmid=None)
        (review_dir / review.articles_screened_file).write_text(
            json.dumps([a.model_dump(mode="json")])
        )
        seen = _build_seen_ids(review, review_dir)
        assert "doi:10.9999/screened" in seen

    def test_loads_from_included_json(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        a = _make_article(doi="10.8888/included", pmid=None)
        (review_dir / review.articles_included_file).write_text(
            json.dumps([a.model_dump(mode="json")])
        )
        seen = _build_seen_ids(review, review_dir)
        assert "doi:10.8888/included" in seen

    def test_merges_all_sources(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        review.seen_article_ids = ["pmid:111"]
        state_manager.save(review, review_dir)

        a_screened = _make_article(doi="10.1/s", pmid=None)
        (review_dir / review.articles_screened_file).write_text(
            json.dumps([a_screened.model_dump(mode="json")])
        )
        a_included = _make_article(doi="10.1/i", pmid=None)
        (review_dir / review.articles_included_file).write_text(
            json.dumps([a_included.model_dump(mode="json")])
        )
        seen = _build_seen_ids(review, review_dir)
        assert "pmid:111" in seen
        assert "doi:10.1/s" in seen
        assert "doi:10.1/i" in seen

    def test_empty_when_no_files(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        seen = _build_seen_ids(review, review_dir)
        assert isinstance(seen, set)




class TestRetrieveForQueriesConfig:
    """Regression: --retrieve passed the APIConfig dataclass to code that called
    cfg.get(...), so Checkpoint 6 iteration and /lit-review-update crashed with
    AttributeError before any request was made."""

    async def test_accepts_api_config_and_passes_ncbi_key(self):
        from unittest.mock import AsyncMock, patch

        from src.integrations.config import APIConfig
        from src.skills.iteration_manager import _retrieve_for_queries

        article = Article(pmid="999", title="New iteration hit", source_dbs=["pubmed"])
        with patch("src.skills.iteration_manager.PubMedClient") as client_cls:
            client = client_cls.return_value
            client.search = AsyncMock(return_value={"pmids": ["999"]})
            client.fetch_abstracts = AsyncMock(return_value=[article])
            new, hits, _ = await _retrieve_for_queries(
                APIConfig(ncbi_api_key="ncbi-key"), ["heart failure"], set()
            )

        client_cls.assert_called_once_with("ncbi-key")
        assert [a.pmid for a in new] == ["999"]
        assert hits == {"heart failure": 1}
