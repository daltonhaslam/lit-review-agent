"""Batch-C regression test — enricher Stage 12 match-back on s2_id.

Reproduces the bug where a forward-chased article with s2_id set but
doi=None AND pmid=None is fetched by batch_lookup (via its bare s2_id) but
never matched back, so citation_count/tldr returned by the API are silently
discarded. All external API calls are mocked; no live network access.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import state as state_manager
from src.integrations.config import APIConfig
from src.models.article import Article
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType
from src.skills.enricher import enrich


def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Adult patients hospitalized with heart failure",
        I="Care transitions interventions",
        C="Usual care",
        O=["30-day all-cause readmission rate", "all-cause mortality"],
        inclusion_criteria=["Adults ≥18", "Primary diagnosis of heart failure"],
        exclusion_criteria=["Pediatric population"],
    )
    defaults.update(kwargs)
    return PICO(**defaults)


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
    path = review_dir / "articles_included.json"
    path.write_text(json.dumps([a.model_dump(mode="json") for a in articles], indent=2))


class TestEnrichS2IdMatchBack:
    @pytest.mark.asyncio
    async def test_enriches_article_matched_only_by_s2_id(self, tmp_path):
        """A forward-chased article (s2_id set, doi/pmid None) must be enriched
        from the S2 record the API returns for its bare s2_id."""
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.data_extracted)
        _write_included(
            review_dir,
            [_make_article(pmid=None, doi=None, s2_id="s2paper123")],
        )

        # batch_lookup returns a record carrying the same s2_id + enrichment.
        s2_record = _make_article(
            pmid=None,
            doi=None,
            s2_id="s2paper123",
            citation_count=42,
            tldr="Care transitions cut readmissions.",
        )
        mock_s2 = AsyncMock()
        mock_s2.batch_lookup = AsyncMock(return_value=[s2_record])
        mock_oa = MagicMock()
        mock_oa.get_work = AsyncMock(return_value=None)

        with (
            patch("src.skills.enricher.load_api_config", return_value=APIConfig()),
            patch("src.skills.enricher.SemanticScholarClient", return_value=mock_s2),
            patch("src.skills.enricher.OpenAlexClient", return_value=mock_oa),
        ):
            result = await enrich(review_dir)

        assert result["s2_matched"] == 1

        articles = [
            Article(**a)
            for a in json.loads((review_dir / "articles_included.json").read_text())
        ]
        assert articles[0].citation_count == 42
        assert articles[0].tldr == "Care transitions cut readmissions."
