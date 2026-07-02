"""Tests for Phase 1 multi_source_retriever year_range fix (Issue #35).

Covers:
  - _search_pubmed appends PubMed date filter when year_range is set
  - _search_europepmc appends FIRST_PDATE filter when year_range is set
  - No filter appended when year_range is None
  - retrieve() passes year_range to both PubMed and EuropePMC tasks
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.skills.multi_source_retriever import _search_europepmc, _search_pubmed


# ---------------------------------------------------------------------------
# _search_pubmed year_range
# ---------------------------------------------------------------------------

class TestSearchPubmedYearRange:
    def _make_pubmed_client(self, captured_queries: list[str]):
        client = MagicMock()
        async def fake_search(query, max_results):
            captured_queries.append(query)
            return {"pmids": []}
        client.search = fake_search
        return client

    def test_no_year_range_passes_query_unchanged(self):
        captured: list[str] = []
        base_query = '"Heart Failure"[Mesh] AND Humans[Mesh]'

        async def run():
            client = self._make_pubmed_client(captured)
            with patch(
                "src.skills.multi_source_retriever.PubMedClient",
                return_value=client,
            ):
                cfg = MagicMock()
                cfg.ncbi_api_key = None
                return await _search_pubmed(base_query, 10, cfg, year_range=None)

        asyncio.run(run())
        assert len(captured) == 1
        assert "[pdat]" not in captured[0]
        assert captured[0] == base_query

    def test_year_range_appends_pdat_filter(self):
        captured: list[str] = []
        base_query = '"Heart Failure"[Mesh] AND Humans[Mesh]'

        async def run():
            client = self._make_pubmed_client(captured)
            with patch(
                "src.skills.multi_source_retriever.PubMedClient",
                return_value=client,
            ):
                cfg = MagicMock()
                cfg.ncbi_api_key = None
                return await _search_pubmed(base_query, 10, cfg, year_range=(2015, 2024))

        asyncio.run(run())
        assert len(captured) == 1
        assert "2015:2024[pdat]" in captured[0]
        assert base_query in captured[0]

    def test_year_range_filter_format(self):
        captured: list[str] = []
        base_query = "heart failure readmission"

        async def run():
            client = self._make_pubmed_client(captured)
            with patch(
                "src.skills.multi_source_retriever.PubMedClient",
                return_value=client,
            ):
                cfg = MagicMock()
                cfg.ncbi_api_key = None
                return await _search_pubmed(base_query, 10, cfg, year_range=(2010, 2023))

        asyncio.run(run())
        # Should be " AND (2010:2023[pdat])"
        assert " AND (2010:2023[pdat])" in captured[0]


# ---------------------------------------------------------------------------
# _search_europepmc year_range
# ---------------------------------------------------------------------------

class TestSearchEuropePMCYearRange:
    def _make_epmc_client(self, captured_queries: list[str]):
        client = MagicMock()
        async def fake_search(query, max_results):
            captured_queries.append(query)
            return []
        client.search = fake_search
        return client

    def test_no_year_range_passes_query_unchanged(self):
        captured: list[str] = []
        base_query = "heart failure readmission"

        async def run():
            client = self._make_epmc_client(captured)
            with patch(
                "src.skills.multi_source_retriever.EuropePMCClient",
                return_value=client,
            ):
                return await _search_europepmc(base_query, 10, year_range=None)

        asyncio.run(run())
        assert len(captured) == 1
        assert "FIRST_PDATE" not in captured[0]
        assert captured[0] == base_query

    def test_year_range_appends_first_pdate_filter(self):
        captured: list[str] = []
        base_query = "heart failure readmission"

        async def run():
            client = self._make_epmc_client(captured)
            with patch(
                "src.skills.multi_source_retriever.EuropePMCClient",
                return_value=client,
            ):
                return await _search_europepmc(base_query, 10, year_range=(2015, 2024))

        asyncio.run(run())
        assert len(captured) == 1
        assert "FIRST_PDATE:[2015 TO 2024]" in captured[0]
        assert base_query in captured[0]

    def test_year_range_filter_format(self):
        captured: list[str] = []
        base_query = "pediatric observation hospitalization"

        async def run():
            client = self._make_epmc_client(captured)
            with patch(
                "src.skills.multi_source_retriever.EuropePMCClient",
                return_value=client,
            ):
                return await _search_europepmc(base_query, 10, year_range=(2010, 2025))

        asyncio.run(run())
        assert " AND FIRST_PDATE:[2010 TO 2025]" in captured[0]
