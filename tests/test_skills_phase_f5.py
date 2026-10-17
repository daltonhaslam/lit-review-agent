"""Unit tests for Phase F5 — CC identifier enrichment (Issue #39).

Covers:
  _enrich_missing_identifiers()  — CrossRef → S2 → PubMed recovery cascade
  PubMedClient.search_by_title() — exact title search returning at most one PMID
  SemanticScholarClient.search_paper() — title-similarity paper lookup
  enrich_cc() CLI mode           — reads articles_screened.json, enriches, writes back
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import state as state_manager
from src.integrations.pubmed_client import PubMedClient
from src.integrations.semantic_scholar_client import SemanticScholarClient
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.citation_chaser import _enrich_missing_identifiers, enrich_cc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_article(**kwargs) -> Article:
    defaults = dict(
        title="Telemedicine for pediatric patients in community hospitals",
        authors=["Smith J"],
        year=2022,
        journal="Pediatrics",
        source_dbs=["semantic_scholar"],
    )
    defaults.update(kwargs)
    return Article(**defaults)


def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-review-f5"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="test-review-f5",
        raw_question="Test question",
        review_type=ReviewType.scoping,
        screening_mode=ScreeningMode.thorough,
        status=ReviewStatus.citation_chased,
        pico=PICO(
            P="Pediatric patients",
            I="Telemedicine",
            C="No comparator",
            O=["Outcomes"],
            inclusion_criteria=["Age < 18"],
            exclusion_criteria=["Adults"],
        ),
        queries=QuerySet(
            pubmed=[Query(step_number="#1", query_string='"Pediatrics"[Mesh]', database="pubmed")],
        ),
    )
    defaults.update(kwargs)
    review = Review(**defaults)
    state_manager.save(review, review_dir)
    return review, review_dir


def _write_screened(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_screened.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _make_clients():
    crossref = MagicMock()
    s2 = MagicMock()
    pubmed = MagicMock()
    return crossref, s2, pubmed


# ---------------------------------------------------------------------------
# _enrich_missing_identifiers — CrossRef recovery
# ---------------------------------------------------------------------------

class TestEnrichMissingIdentifiersCrossRef:
    def test_crossref_recovery_sets_doi(self):
        article = _make_article()
        crossref, s2, pubmed = _make_clients()
        crossref.search_by_title = AsyncMock(return_value="10.1/recovered")

        stats = asyncio.run(_enrich_missing_identifiers([article], crossref, s2, pubmed))

        assert article.doi == "10.1/recovered"
        assert stats["recovered_by_crossref"] == 1
        assert stats["recovered_by_s2"] == 0
        assert stats["recovered_by_pubmed"] == 0
        assert stats["still_missing"] == 0
        assert stats["candidates_missing_ids"] == 1

    def test_crossref_stops_cascade_when_doi_found(self):
        article = _make_article()
        crossref, s2, pubmed = _make_clients()
        crossref.search_by_title = AsyncMock(return_value="10.1/found")
        s2.search_paper = AsyncMock()

        asyncio.run(_enrich_missing_identifiers([article], crossref, s2, pubmed))

        s2.search_paper.assert_not_called()

    def test_articles_with_existing_doi_skipped(self):
        article = _make_article(doi="10.1/existing")
        crossref, s2, pubmed = _make_clients()
        crossref.search_by_title = AsyncMock(return_value="10.1/other")

        stats = asyncio.run(_enrich_missing_identifiers([article], crossref, s2, pubmed))

        assert article.doi == "10.1/existing"  # unchanged
        assert stats["candidates_missing_ids"] == 0
        crossref.search_by_title.assert_not_called()

    def test_articles_with_existing_pmid_skipped(self):
        article = _make_article(pmid="12345678")
        crossref, s2, pubmed = _make_clients()
        crossref.search_by_title = AsyncMock(return_value="10.1/found")

        stats = asyncio.run(_enrich_missing_identifiers([article], crossref, s2, pubmed))

        assert article.pmid == "12345678"  # unchanged
        assert stats["candidates_missing_ids"] == 0


# ---------------------------------------------------------------------------
# _enrich_missing_identifiers — S2 fallback
# ---------------------------------------------------------------------------

class TestEnrichMissingIdentifiersS2:
    def test_s2_fallback_sets_doi_pmid_s2id(self):
        article = _make_article()
        crossref, s2, pubmed = _make_clients()
        crossref.search_by_title = AsyncMock(return_value=None)
        s2_result = _make_article(doi="10.1/s2", pmid="99999", s2_id="abc123")
        s2.search_paper = AsyncMock(return_value=s2_result)

        stats = asyncio.run(_enrich_missing_identifiers([article], crossref, s2, pubmed))

        assert article.doi == "10.1/s2"
        assert article.pmid == "99999"
        assert article.s2_id == "abc123"
        assert stats["recovered_by_s2"] == 1
        assert stats["recovered_by_crossref"] == 0

    def test_s2_fallback_stops_cascade_when_identifier_found(self):
        article = _make_article()
        crossref, s2, pubmed = _make_clients()
        crossref.search_by_title = AsyncMock(return_value=None)
        s2_result = _make_article(doi="10.1/s2")
        s2.search_paper = AsyncMock(return_value=s2_result)
        pubmed.search_by_title = AsyncMock()

        asyncio.run(_enrich_missing_identifiers([article], crossref, s2, pubmed))

        pubmed.search_by_title.assert_not_called()

    def test_s2_result_with_no_identifiers_falls_through_to_pubmed(self):
        article = _make_article()
        crossref, s2, pubmed = _make_clients()
        crossref.search_by_title = AsyncMock(return_value=None)
        s2_result = _make_article()  # no doi, no pmid
        s2.search_paper = AsyncMock(return_value=s2_result)
        pubmed.search_by_title = AsyncMock(return_value="11111111")

        stats = asyncio.run(_enrich_missing_identifiers([article], crossref, s2, pubmed))

        assert article.pmid == "11111111"
        assert stats["recovered_by_pubmed"] == 1
        assert stats["recovered_by_s2"] == 0


# ---------------------------------------------------------------------------
# _enrich_missing_identifiers — PubMed fallback
# ---------------------------------------------------------------------------

class TestEnrichMissingIdentifiersPubMed:
    def test_pubmed_fallback_sets_pmid(self):
        article = _make_article()
        crossref, s2, pubmed = _make_clients()
        crossref.search_by_title = AsyncMock(return_value=None)
        s2.search_paper = AsyncMock(return_value=None)
        pubmed.search_by_title = AsyncMock(return_value="87654321")

        stats = asyncio.run(_enrich_missing_identifiers([article], crossref, s2, pubmed))

        assert article.pmid == "87654321"
        assert stats["recovered_by_pubmed"] == 1
        assert stats["still_missing"] == 0

    def test_all_three_fail_increments_still_missing(self):
        article = _make_article()
        crossref, s2, pubmed = _make_clients()
        crossref.search_by_title = AsyncMock(return_value=None)
        s2.search_paper = AsyncMock(return_value=None)
        pubmed.search_by_title = AsyncMock(return_value=None)

        stats = asyncio.run(_enrich_missing_identifiers([article], crossref, s2, pubmed))

        assert article.doi is None
        assert article.pmid is None
        assert stats["still_missing"] == 1
        assert stats["recovered_by_crossref"] == 0
        assert stats["recovered_by_s2"] == 0
        assert stats["recovered_by_pubmed"] == 0

    def test_article_without_title_counted_as_still_missing(self):
        article = _make_article(title="")
        crossref, s2, pubmed = _make_clients()
        crossref.search_by_title = AsyncMock()

        stats = asyncio.run(_enrich_missing_identifiers([article], crossref, s2, pubmed))

        assert stats["still_missing"] == 1
        crossref.search_by_title.assert_not_called()

    def test_mixed_batch_recovery_stats(self):
        a1 = _make_article(title="Paper A")
        a2 = _make_article(title="Paper B")
        a3 = _make_article(doi="10.1/exists", title="Paper C")  # already identified
        crossref, s2, pubmed = _make_clients()
        crossref.search_by_title = AsyncMock(side_effect=["10.1/a", None])
        s2.search_paper = AsyncMock(return_value=None)
        pubmed.search_by_title = AsyncMock(return_value=None)

        stats = asyncio.run(_enrich_missing_identifiers([a1, a2, a3], crossref, s2, pubmed))

        assert a1.doi == "10.1/a"
        assert a2.doi is None
        assert stats["candidates_missing_ids"] == 2
        assert stats["recovered_by_crossref"] == 1
        assert stats["still_missing"] == 1

    def test_crossref_exception_falls_through_to_s2(self):
        article = _make_article()
        crossref, s2, pubmed = _make_clients()
        crossref.search_by_title = AsyncMock(side_effect=Exception("timeout"))
        s2_result = _make_article(doi="10.1/s2")
        s2.search_paper = AsyncMock(return_value=s2_result)

        stats = asyncio.run(_enrich_missing_identifiers([article], crossref, s2, pubmed))

        assert article.doi == "10.1/s2"
        assert stats["recovered_by_s2"] == 1


# ---------------------------------------------------------------------------
# PubMedClient.search_by_title
# ---------------------------------------------------------------------------

class TestPubMedSearchByTitle:
    def _make_esearch_response(self, ids: list[str]) -> str:
        return json.dumps({"esearchresult": {"idlist": ids}})

    def test_single_match_returns_pmid(self):
        client = PubMedClient()
        with patch.object(client, "_get", new=AsyncMock(return_value=self._make_esearch_response(["12345678"]))):
            result = asyncio.run(client.search_by_title("Exact title here"))
        assert result == "12345678"

    def test_ambiguous_two_matches_returns_none(self):
        client = PubMedClient()
        with patch.object(client, "_get", new=AsyncMock(return_value=self._make_esearch_response(["11111111", "22222222"]))):
            result = asyncio.run(client.search_by_title("Ambiguous title"))
        assert result is None

    def test_no_match_returns_none(self):
        client = PubMedClient()
        with patch.object(client, "_get", new=AsyncMock(return_value=self._make_esearch_response([]))):
            result = asyncio.run(client.search_by_title("Unknown title"))
        assert result is None

    def test_query_uses_title_field_tag(self):
        client = PubMedClient()
        captured_params = {}

        async def _fake_get(endpoint: str, params: dict) -> str:
            captured_params.update(params)
            return json.dumps({"esearchresult": {"idlist": []}})

        with patch.object(client, "_get", new=_fake_get):
            asyncio.run(client.search_by_title("My Exact Title"))

        assert '"My Exact Title"[Title]' in captured_params.get("term", "")


# ---------------------------------------------------------------------------
# SemanticScholarClient.search_paper
# ---------------------------------------------------------------------------

class TestS2SearchPaper:
    def _make_s2_article(self, title: str, doi: str = None, pmid: str = None) -> Article:
        return Article(
            title=title,
            doi=doi,
            pmid=pmid,
            s2_id="s2abc",
            year=2022,
            source_dbs=["semantic_scholar"],
        )

    def test_high_similarity_returns_article(self):
        client = SemanticScholarClient()
        target_title = "Telemedicine for pediatric subspecialty consultation"
        result_article = self._make_s2_article(target_title, doi="10.1/found")
        with patch.object(client, "search", new=AsyncMock(return_value=[result_article])):
            result = asyncio.run(client.search_paper(target_title))
        assert result is not None
        assert result.doi == "10.1/found"

    def test_low_similarity_returns_none(self):
        client = SemanticScholarClient()
        result_article = self._make_s2_article("Completely unrelated article about adult cardiology")
        with patch.object(client, "search", new=AsyncMock(return_value=[result_article])):
            result = asyncio.run(client.search_paper("Telemedicine for pediatric subspecialty consultation"))
        assert result is None

    def test_empty_results_returns_none(self):
        client = SemanticScholarClient()
        with patch.object(client, "search", new=AsyncMock(return_value=[])):
            result = asyncio.run(client.search_paper("Obscure title"))
        assert result is None

    def test_search_exception_returns_none(self):
        client = SemanticScholarClient()
        with patch.object(client, "search", new=AsyncMock(side_effect=Exception("rate limit"))):
            result = asyncio.run(client.search_paper("Any title"))
        assert result is None

    def test_exact_match_threshold_boundary(self):
        client = SemanticScholarClient()
        # Same title → ratio=1.0, well above 0.85
        title = "Telehealth outcomes for neonatal patients"
        result_article = self._make_s2_article(title, doi="10.1/exact")
        with patch.object(client, "search", new=AsyncMock(return_value=[result_article])):
            result = asyncio.run(client.search_paper(title))
        assert result is not None


# ---------------------------------------------------------------------------
# enrich_cc CLI mode
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("no_citation_metadata_network")
class TestEnrichCC:
    def test_enrich_cc_updates_screened_file(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        cc_article = _make_article(citation_chased=True, screening_decision=ScreeningDecision.include)
        non_cc = _make_article(title="Original screened", doi="10.1/orig", pmid="00000001")
        _write_screened(review_dir, [non_cc, cc_article])

        with (
            patch("src.skills.citation_chaser.CrossRefClient") as MockCR,
            patch("src.skills.citation_chaser.SemanticScholarClient") as MockS2,
            patch("src.skills.citation_chaser.PubMedClient") as MockPM,
        ):
            MockCR.return_value.search_by_title = AsyncMock(return_value="10.1/recovered")
            MockS2.return_value.search_paper = AsyncMock(return_value=None)
            MockPM.return_value.search_by_title = AsyncMock(return_value=None)

            enrich_cc(review_dir)

        capsys.readouterr()
        updated = json.loads((review_dir / "articles_screened.json").read_text())
        cc_updated = next(a for a in updated if a.get("citation_chased"))
        assert cc_updated["doi"] == "10.1/recovered"

    def test_enrich_cc_does_not_touch_non_cc_articles(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        orig = _make_article(title="Original", doi="10.1/keep")
        cc_article = _make_article(citation_chased=True, screening_decision=ScreeningDecision.include)
        _write_screened(review_dir, [orig, cc_article])

        with (
            patch("src.skills.citation_chaser.CrossRefClient") as MockCR,
            patch("src.skills.citation_chaser.SemanticScholarClient") as MockS2,
            patch("src.skills.citation_chaser.PubMedClient") as MockPM,
        ):
            MockCR.return_value.search_by_title = AsyncMock(return_value="10.1/new")
            MockS2.return_value.search_paper = AsyncMock(return_value=None)
            MockPM.return_value.search_by_title = AsyncMock(return_value=None)

            enrich_cc(review_dir)

        capsys.readouterr()
        updated = json.loads((review_dir / "articles_screened.json").read_text())
        orig_updated = next(a for a in updated if not a.get("citation_chased"))
        assert orig_updated["doi"] == "10.1/keep"

    def test_enrich_cc_no_missing_identifiers_still_runs_metadata(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        # CC article already has doi — targets list is empty, but metadata enrichment still runs
        cc_article = _make_article(
            citation_chased=True,
            doi="10.1/already",
            screening_decision=ScreeningDecision.include,
        )
        _write_screened(review_dir, [cc_article])

        with (
            patch("src.skills.citation_chaser.CrossRefClient"),
            patch("src.skills.citation_chaser.SemanticScholarClient"),
            patch("src.skills.citation_chaser.PubMedClient"),
            patch("src.skills.citation_chaser.OpenAlexClient"),
        ):
            enrich_cc(review_dir)

        out = json.loads(capsys.readouterr().out)
        assert "enrichment" in out
        assert out["enrichment"]["candidates_missing_ids"] == 0
        assert "metadata_enrichment" in out

    def test_enrich_cc_appends_to_search_log(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        cc_article = _make_article(citation_chased=True, screening_decision=ScreeningDecision.include)
        _write_screened(review_dir, [cc_article])

        with (
            patch("src.skills.citation_chaser.CrossRefClient") as MockCR,
            patch("src.skills.citation_chaser.SemanticScholarClient") as MockS2,
            patch("src.skills.citation_chaser.PubMedClient") as MockPM,
            patch("src.skills.citation_chaser.OpenAlexClient"),
        ):
            MockCR.return_value.search_by_title = AsyncMock(return_value="10.1/rec")
            MockS2.return_value.search_paper = AsyncMock(return_value=None)
            MockPM.return_value.search_by_title = AsyncMock(return_value=None)

            enrich_cc(review_dir)

        capsys.readouterr()
        log_path = review_dir / "search_log.md"
        assert log_path.exists()
        log_content = log_path.read_text()
        assert "CC Identifier + Metadata Enrichment" in log_content
