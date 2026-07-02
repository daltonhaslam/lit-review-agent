"""Issue #49: citation_chased articles must arrive at Stage 12 with full
metadata (authors, journal, year, volume, issue, pages, abstract) — not just
PMID/DOI. The enrichment runs CrossRef → OpenAlex → S2 sequentially per
article; failures are non-blocking (article keeps whatever it had)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.article import Article
from src.skills.citation_chaser import _enrich_missing_metadata


@pytest.mark.asyncio
async def test_crossref_fills_authors_journal_year():
    article = Article(doi="10.1/x", title="t")
    crossref = MagicMock()
    crossref.resolve_doi = AsyncMock(return_value=Article(
        doi="10.1/x", title="t", authors=["Smith J"], journal="JAMA",
        year=2020, volume="324", issue="1", pages="100-110",
    ))
    openalex = MagicMock()
    openalex.get_work = AsyncMock(return_value=None)
    s2 = MagicMock()
    s2.batch_lookup = AsyncMock(return_value=[])

    stats = await _enrich_missing_metadata([article], crossref, openalex, s2)

    assert article.authors == ["Smith J"]
    assert article.journal == "JAMA"
    assert article.year == 2020
    assert article.volume == "324"
    assert stats["enriched_by_crossref"] == 1


@pytest.mark.asyncio
async def test_openalex_used_when_crossref_misses():
    article = Article(doi="10.1/y", title="t")
    crossref = MagicMock(); crossref.resolve_doi = AsyncMock(return_value=None)
    openalex = MagicMock()
    openalex.get_work = AsyncMock(return_value=Article(
        doi="10.1/y", title="t", authors=["Lee K"], journal="NEJM", year=2021,
    ))
    s2 = MagicMock(); s2.batch_lookup = AsyncMock(return_value=[])

    stats = await _enrich_missing_metadata([article], crossref, openalex, s2)

    assert article.authors == ["Lee K"]
    assert article.journal == "NEJM"
    assert stats["enriched_by_openalex"] == 1


@pytest.mark.asyncio
async def test_skips_when_already_complete():
    """Articles with all metadata fields populated must not trigger any API calls."""
    article = Article(doi="10.1/z", title="t", authors=["A B"], journal="J", year=2020,
                      volume="1", issue="1", pages="1-2", abstract="x")
    crossref = MagicMock(); crossref.resolve_doi = AsyncMock()
    openalex = MagicMock(); openalex.get_work = AsyncMock()
    s2 = MagicMock(); s2.batch_lookup = AsyncMock()

    stats = await _enrich_missing_metadata([article], crossref, openalex, s2)

    crossref.resolve_doi.assert_not_called()
    openalex.get_work.assert_not_called()
    s2.batch_lookup.assert_not_called()
    assert stats["already_complete"] == 1


@pytest.mark.asyncio
async def test_handles_articles_without_doi():
    """Articles without DOI cannot be enriched; they're counted but not crashed."""
    article = Article(pmid="123", title="t")
    crossref = MagicMock(); crossref.resolve_doi = AsyncMock()
    openalex = MagicMock(); openalex.get_work = AsyncMock()
    s2 = MagicMock(); s2.batch_lookup = AsyncMock()

    stats = await _enrich_missing_metadata([article], crossref, openalex, s2)

    crossref.resolve_doi.assert_not_called()
    assert stats["no_doi"] == 1


@pytest.mark.asyncio
async def test_crossref_failure_falls_through_to_openalex():
    """If CrossRef raises, the article still gets a chance via OpenAlex."""
    article = Article(doi="10.1/q", title="t")
    crossref = MagicMock()
    crossref.resolve_doi = AsyncMock(side_effect=Exception("network"))
    openalex = MagicMock()
    openalex.get_work = AsyncMock(return_value=Article(
        doi="10.1/q", title="t", authors=["Z W"], journal="Lancet", year=2022,
    ))
    s2 = MagicMock(); s2.batch_lookup = AsyncMock(return_value=[])

    stats = await _enrich_missing_metadata([article], crossref, openalex, s2)

    assert article.authors == ["Z W"]
    assert stats["enriched_by_openalex"] == 1
    assert stats["crossref_errors"] == 1
