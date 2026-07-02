"""Unit tests for Phase 2 — Retrieval skill modules and API clients.

All network calls are mocked. No live API access.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import state as state_manager
from src.integrations.crossref_client import _parse_crossref_work
from src.integrations.europepmc_client import _parse_europepmc_result
from src.integrations.openalex_client import _reconstruct_abstract, _parse_openalex_work
from src.integrations.semantic_scholar_client import _parse_s2_paper
from src.models.article import Article
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills import deduplicator
from src.skills.deduplicator import _normalize_doi, _normalize_title, deduplicate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-chf-2026-04"
    review_dir.mkdir()
    pico = PICO(
        P="Heart failure patients",
        I="Care transitions interventions",
        C="Usual care",
        O=["30-day readmission rate"],
    )
    queries = QuerySet(
        pubmed=[
            Query(step_number="#1", query_string='"Heart Failure"[Mesh]', database="pubmed"),
            Query(
                step_number="#2",
                query_string="(#1) AND Humans[Mesh] AND English[lang]",
                database="pubmed",
            ),
        ],
        openalex=[Query(step_number="#1", query_string="heart failure care transitions readmission", database="openalex")],
        semantic_scholar=[Query(step_number="#1", query_string="heart failure readmission transitions", database="semantic_scholar")],
    )
    review = Review(
        review_id="test-chf-2026-04",
        raw_question="Does care transitions reduce CHF readmission?",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        pico=pico,
        queries=queries,
        status=ReviewStatus.queries_built,
        **kwargs,
    )
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


def _write_raw(review_dir: Path, articles: list[Article]) -> None:
    path = review_dir / "articles_raw.json"
    path.write_text(json.dumps([a.model_dump(mode="json") for a in articles], indent=2))


# ---------------------------------------------------------------------------
# openalex_client parsers
# ---------------------------------------------------------------------------

class TestOpenAlexParsers:
    def test_reconstruct_abstract_basic(self):
        inv = {"Heart": [0], "failure": [1], "readmission": [2]}
        result = _reconstruct_abstract(inv)
        assert result == "Heart failure readmission"

    def test_reconstruct_abstract_none(self):
        assert _reconstruct_abstract(None) is None
        assert _reconstruct_abstract({}) is None

    def test_parse_openalex_work_minimal(self):
        work = {
            "id": "https://openalex.org/W123",
            "title": "Heart failure care transitions",
            "doi": "https://doi.org/10.1001/test",
            "authorships": [{"author": {"display_name": "Smith J"}}],
            "publication_year": 2022,
            "primary_location": {"source": {"display_name": "NEJM"}},
            "cited_by_count": 42,
            "open_access": {"is_oa": True, "oa_url": "https://example.com/pdf"},
            "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/12345678/"},
            "abstract_inverted_index": {"Abstract": [0], "text": [1]},
            "concepts": [{"display_name": "Medicine", "score": 0.9}],
        }
        article = _parse_openalex_work(work)
        assert article is not None
        assert article.title == "Heart failure care transitions"
        assert article.doi == "10.1001/test"
        assert article.pmid == "12345678"
        assert article.year == 2022
        assert article.citation_count == 42
        assert article.oa_status is True
        assert "openalex" in article.source_dbs

    def test_parse_openalex_work_no_title_returns_none(self):
        assert _parse_openalex_work({"title": "", "cited_by_count": 0}) is None

    def test_parse_openalex_work_handles_missing_fields(self):
        work = {"title": "Minimal work", "cited_by_count": 0}
        article = _parse_openalex_work(work)
        assert article is not None
        assert article.authors == []
        assert article.doi is None


# ---------------------------------------------------------------------------
# crossref_client parsers
# ---------------------------------------------------------------------------

class TestCrossRefParsers:
    def test_parse_crossref_work_full(self):
        work = {
            "title": ["Transitions of care in heart failure"],
            "DOI": "10.1001/cardio.2022.001",
            "author": [{"family": "Jones", "given": "Alice"}],
            "published-print": {"date-parts": [[2022, 3, 1]]},
            "container-title": ["JAMA Cardiology"],
            "volume": "7",
            "issue": "3",
            "page": "100-110",
        }
        article = _parse_crossref_work(work)
        assert article is not None
        assert article.doi == "10.1001/cardio.2022.001"
        assert article.year == 2022
        assert article.journal == "JAMA Cardiology"
        assert "Jones" in article.authors[0]

    def test_parse_crossref_work_no_title_returns_none(self):
        assert _parse_crossref_work({"title": []}) is None

    def test_parse_crossref_work_falls_back_to_online_date(self):
        work = {
            "title": ["Some title"],
            "published-online": {"date-parts": [[2021, 6, 15]]},
        }
        article = _parse_crossref_work(work)
        assert article is not None
        assert article.year == 2021


# ---------------------------------------------------------------------------
# semantic_scholar_client parsers
# ---------------------------------------------------------------------------

class TestSemanticScholarParsers:
    def test_parse_s2_paper_full(self):
        paper = {
            "paperId": "abc123",
            "title": "Heart failure readmission interventions",
            "abstract": "A systematic review of care transitions.",
            "authors": [{"name": "Smith J"}, {"name": "Jones A"}],
            "year": 2021,
            "venue": "NEJM",
            "citationCount": 75,
            "influentialCitationCount": 10,
            "tldr": {"text": "Care transitions reduce readmission."},
            "isOpenAccess": False,
            "openAccessPdf": None,
            "externalIds": {"DOI": "10.1056/nejm.2021.001", "PubMed": "33000001"},
        }
        article = _parse_s2_paper(paper)
        assert article is not None
        assert article.s2_id == "abc123"
        assert article.doi == "10.1056/nejm.2021.001"
        assert article.pmid == "33000001"
        assert article.tldr == "Care transitions reduce readmission."
        assert article.citation_count == 75
        assert "semantic_scholar" in article.source_dbs

    def test_parse_s2_paper_no_title_returns_none(self):
        assert _parse_s2_paper({"title": None, "paperId": "x"}) is None

    def test_parse_s2_paper_missing_external_ids(self):
        paper = {"paperId": "xyz", "title": "Some paper", "externalIds": {}}
        article = _parse_s2_paper(paper)
        assert article is not None
        assert article.doi is None
        assert article.pmid is None


# ---------------------------------------------------------------------------
# europepmc_client parsers
# ---------------------------------------------------------------------------

class TestEuropePMCParsers:
    def test_parse_europepmc_result_full(self):
        result = {
            "pmid": "98765432",
            "doi": "10.1001/test.2020",
            "title": "Readmission reduction in heart failure",
            "authorList": {
                "author": [
                    {"fullName": "Brown K"},
                    {"lastName": "White", "firstName": "M"},
                ]
            },
            "pubYear": "2020",
            "journalTitle": "Circulation",
            "abstractText": "We studied 500 patients.",
            "citedByCount": 30,
            "isOpenAccess": "Y",
            "fullTextUrlList": {
                "fullTextUrl": [
                    {"documentStyle": "pdf", "url": "https://example.com/article.pdf"}
                ]
            },
        }
        article = _parse_europepmc_result(result)
        assert article is not None
        assert article.pmid == "98765432"
        assert article.doi == "10.1001/test.2020"
        assert article.year == 2020
        assert article.oa_status is True
        assert article.oa_pdf_url == "https://example.com/article.pdf"
        assert "europepmc" in article.source_dbs

    def test_parse_europepmc_result_no_title_returns_none(self):
        assert _parse_europepmc_result({"title": ""}) is None

    def test_parse_europepmc_result_not_oa(self):
        result = {"pmid": "1", "title": "Some study", "isOpenAccess": "N"}
        article = _parse_europepmc_result(result)
        assert article is not None
        assert article.oa_status is False


# ---------------------------------------------------------------------------
# deduplicator
# ---------------------------------------------------------------------------

class TestNormalizeDoi:
    def test_strips_https_prefix(self):
        assert _normalize_doi("https://doi.org/10.1001/test") == "10.1001/test"

    def test_lowercases(self):
        assert _normalize_doi("10.1001/TEST") == "10.1001/test"

    def test_none_input(self):
        assert _normalize_doi(None) is None

    def test_empty_string(self):
        assert _normalize_doi("") is None


class TestNormalizeTitle:
    def test_lowercase_and_strip_punctuation(self):
        result = _normalize_title("Heart Failure: A Review!")
        assert result == "heart failure a review"

    def test_extra_whitespace_collapsed(self):
        result = _normalize_title("  Heart   Failure  ")
        assert "  " not in result


class TestRunDedup:
    def test_doi_match_deduplicates(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        articles = [
            _make_article(doi="10.1001/test", source_dbs=["pubmed"]),
            _make_article(doi="10.1001/test", source_dbs=["openalex"]),
        ]
        _write_raw(review_dir, articles)
        # Also set status to aggregated so deduplicator accepts it
        review = state_manager.load(review_dir)
        review.status = ReviewStatus.aggregated
        state_manager.save(review, review_dir)

        result = deduplicate(review_dir)
        assert result["after"] == 1
        assert result["removed"] == 1
        assert result["by_method"]["doi_match"] == 1

        saved = state_manager.load(review_dir)
        assert saved.status == ReviewStatus.deduplicated
        assert saved.prisma_counts.identified == 1

    def test_doi_match_merges_source_dbs(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        articles = [
            _make_article(doi="10.1001/test", source_dbs=["pubmed"]),
            _make_article(doi="10.1001/test", source_dbs=["openalex"]),
        ]
        _write_raw(review_dir, articles)
        review = state_manager.load(review_dir)
        review.status = ReviewStatus.aggregated
        state_manager.save(review, review_dir)

        deduplicate(review_dir)

        deduped_path = review_dir / "articles_deduped.json"
        deduped = [Article(**a) for a in json.loads(deduped_path.read_text())]
        assert sorted(deduped[0].source_dbs) == ["openalex", "pubmed"]

    def test_title_similarity_deduplicates(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        articles = [
            _make_article(
                title="Transitions of care reduce readmission in heart failure patients",
                doi=None,
                source_dbs=["pubmed"],
            ),
            _make_article(
                title="Transitions of care reduces readmission in heart failure patients",
                doi=None,
                source_dbs=["openalex"],
            ),
        ]
        _write_raw(review_dir, articles)
        review = state_manager.load(review_dir)
        review.status = ReviewStatus.aggregated
        state_manager.save(review, review_dir)

        result = deduplicate(review_dir)
        assert result["after"] == 1
        assert result["by_method"]["title_similarity"] == 1

    def test_distinct_articles_not_merged(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        articles = [
            _make_article(title="Heart failure care transitions reduce readmission", doi="10.1/a"),
            _make_article(title="ICU early mobility protocols reduce hospital LOS", doi="10.1/b"),
            _make_article(title="CDI query compliance best practices review", doi="10.1/c"),
        ]
        _write_raw(review_dir, articles)
        review = state_manager.load(review_dir)
        review.status = ReviewStatus.aggregated
        state_manager.save(review, review_dir)

        result = deduplicate(review_dir)
        assert result["after"] == 3
        assert result["removed"] == 0

    def test_author_year_fallback_deduplicates(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        articles = [
            _make_article(
                title="Early discharge planning in heart failure",
                authors=["Johnson R", "Brown T"],
                year=2020,
                doi=None,
                source_dbs=["pubmed"],
            ),
            _make_article(
                title="Early discharge planning for heart failure patients",
                authors=["Johnson R", "Lee S"],
                year=2020,
                doi=None,
                source_dbs=["europepmc"],
            ),
        ]
        _write_raw(review_dir, articles)
        review = state_manager.load(review_dir)
        review.status = ReviewStatus.aggregated
        state_manager.save(review, review_dir)

        result = deduplicate(review_dir)
        assert result["after"] == 1
        assert result["by_method"]["author_year_fallback"] == 1

    def test_doi_with_https_prefix_matches(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        articles = [
            _make_article(doi="https://doi.org/10.1001/test", source_dbs=["openalex"]),
            _make_article(doi="10.1001/test", source_dbs=["pubmed"]),
        ]
        _write_raw(review_dir, articles)
        review = state_manager.load(review_dir)
        review.status = ReviewStatus.aggregated
        state_manager.save(review, review_dir)

        result = deduplicate(review_dir)
        assert result["after"] == 1

    def test_deduped_file_written(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(doi="10.1/a"), _make_article(doi="10.1/b")]
        _write_raw(review_dir, articles)
        review = state_manager.load(review_dir)
        review.status = ReviewStatus.aggregated
        state_manager.save(review, review_dir)

        deduplicate(review_dir)
        assert (review_dir / "articles_deduped.json").exists()

    def test_prisma_count_updated(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        distinct_titles = [
            "Care transitions reduce heart failure readmission rates",
            "ICU early mobility protocols shorten hospital stay",
            "CDI query compliance improves MS-DRG capture",
            "Palliative care consultation reduces inpatient costs",
            "Physician advisor impact on utilization management",
        ]
        articles = [_make_article(doi=f"10.1/{i}", title=t) for i, t in enumerate(distinct_titles)]
        _write_raw(review_dir, articles)
        review = state_manager.load(review_dir)
        review.status = ReviewStatus.aggregated
        state_manager.save(review, review_dir)

        deduplicate(review_dir)
        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.identified == 5


# ---------------------------------------------------------------------------
# multi_source_retriever (mocked)
# ---------------------------------------------------------------------------

class TestMultiSourceRetriever:
    @pytest.mark.asyncio
    async def test_retrieves_from_all_sources_in_parallel(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)

        pubmed_articles = [_make_article(pmid="111", title="PubMed result", source_dbs=["pubmed"])]
        openalex_articles = [_make_article(doi="10.1/oa", title="OpenAlex result", source_dbs=["openalex"])]
        s2_articles = [_make_article(s2_id="s2abc", title="S2 result", source_dbs=["semantic_scholar"])]

        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=pubmed_articles)),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=openalex_articles)),
            patch("src.skills.multi_source_retriever._search_semantic_scholar", new=AsyncMock(return_value=s2_articles)),
        ):
            result = await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        assert result["total"] == 3
        assert result["by_source"]["pubmed"] == 1
        assert result["by_source"]["openalex"] == 1
        assert result["by_source"]["semantic_scholar"] == 1

        saved = state_manager.load(review_dir)
        assert saved.status == ReviewStatus.retrieved
        assert "pubmed" in saved.sources_searched

    @pytest.mark.asyncio
    async def test_search_log_header_reflects_searched_databases(self, tmp_path):
        # Regression: the "Databases searched" header must name the sources actually searched,
        # not read review.sources_searched (which retrieve() only assigns AFTER writing the log).
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)
        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=[_make_article(pmid="1", source_dbs=["pubmed"])])),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[_make_article(doi="10.1/o", source_dbs=["openalex"])])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar", new=AsyncMock(return_value=[])),
        ):
            await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        content = (review_dir / "search_log.md").read_text()
        assert "**Databases searched:** PubMed, OpenAlex, Semantic Scholar" in content

    @pytest.mark.asyncio
    async def test_articles_raw_file_written(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(pmid="999", title="Test article")]

        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=articles)),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar", new=AsyncMock(return_value=[])),
        ):
            await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        raw_path = review_dir / "articles_raw.json"
        assert raw_path.exists()
        raw = json.loads(raw_path.read_text())
        assert len(raw) == 1
        assert raw[0]["title"] == "Test article"

    @pytest.mark.asyncio
    async def test_source_failure_does_not_abort(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)
        good_articles = [_make_article(pmid="1", title="Good result", source_dbs=["pubmed"])]

        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=good_articles)),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(side_effect=Exception("API down"))),
            patch("src.skills.multi_source_retriever._search_semantic_scholar", new=AsyncMock(return_value=[])),
        ):
            result = await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        assert result["by_source"]["pubmed"] == 1
        assert result["by_source"]["openalex"] == 0

    @pytest.mark.asyncio
    async def test_retrieval_errors_key_always_present(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(pmid="1", title="Result")]

        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=articles)),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar", new=AsyncMock(return_value=[])),
        ):
            result = await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        assert "retrieval_errors" in result
        # pubmed returned results → no error; openalex/s2 returned 0 → zero-result WARNINGs
        assert "pubmed" not in result["retrieval_errors"]
        assert "WARNING" in result["retrieval_errors"]["openalex"]
        assert "WARNING" in result["retrieval_errors"]["semantic_scholar"]

    @pytest.mark.asyncio
    async def test_retrieval_errors_captures_exception_message(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)

        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_openalex", new=AsyncMock(return_value=[])),
            patch("src.skills.multi_source_retriever._search_semantic_scholar",
                  new=AsyncMock(side_effect=Exception("429 Client Error: Too Many Requests"))),
        ):
            result = await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        assert "semantic_scholar" in result["retrieval_errors"]
        assert "429" in result["retrieval_errors"]["semantic_scholar"]

    @pytest.mark.asyncio
    async def test_retrieval_errors_only_failed_sources(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(pmid="1", title="PubMed result")]

        with (
            patch("src.skills.multi_source_retriever._search_pubmed", new=AsyncMock(return_value=articles)),
            patch("src.skills.multi_source_retriever._search_openalex",
                  new=AsyncMock(side_effect=Exception("timeout"))),
            patch("src.skills.multi_source_retriever._search_semantic_scholar", new=AsyncMock(return_value=[])),
        ):
            result = await retrieve(review_dir, sources=["pubmed", "openalex", "semantic_scholar"])

        assert "pubmed" not in result["retrieval_errors"]
        assert "openalex" in result["retrieval_errors"]
        assert "timeout" in result["retrieval_errors"]["openalex"]
        # semantic_scholar returned 0 results (no exception) → zero-result WARNING
        assert "WARNING" in result["retrieval_errors"]["semantic_scholar"]

    @pytest.mark.asyncio
    async def test_raises_when_queries_not_built(self, tmp_path):
        from src.skills.multi_source_retriever import retrieve

        review_dir = tmp_path / "empty-review"
        review_dir.mkdir()
        review = Review(
            review_id="empty-review",
            raw_question="test",
            status=ReviewStatus.pico_formulated,
        )
        state_manager.save(review, review_dir)

        with pytest.raises(ValueError, match="Queries not built"):
            await retrieve(review_dir)


# ---------------------------------------------------------------------------
# aggregator (mocked CrossRef)
# ---------------------------------------------------------------------------

class TestAggregator:
    @pytest.mark.asyncio
    async def test_aggregate_resolves_missing_doi(self, tmp_path):
        from src.skills.aggregator import aggregate

        _, review_dir = _make_review(tmp_path)
        articles = [
            _make_article(pmid="12345678", doi=None, title="Heart failure study"),
        ]
        _write_raw(review_dir, articles)
        review = state_manager.load(review_dir)
        review.status = ReviewStatus.retrieved
        state_manager.save(review, review_dir)

        mock_crossref_get = AsyncMock(return_value={
            "message": {
                "items": [{"DOI": "10.1001/resolved.doi", "score": 90}]
            }
        })

        with patch("src.skills.aggregator.CrossRefClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance._get = mock_crossref_get
            mock_cls.return_value = mock_instance
            result = await aggregate(review_dir)

        saved = state_manager.load(review_dir)
        assert saved.status == ReviewStatus.aggregated
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_aggregate_no_articles_missing_doi(self, tmp_path):
        from src.skills.aggregator import aggregate

        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(doi="10.1/existing", title="Already has DOI")]
        _write_raw(review_dir, articles)
        review = state_manager.load(review_dir)
        review.status = ReviewStatus.retrieved
        state_manager.save(review, review_dir)

        with patch("src.skills.aggregator.CrossRefClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance._get = AsyncMock()
            mock_cls.return_value = mock_instance
            result = await aggregate(review_dir)

        assert result["with_doi"] == 1
        assert result["without_doi"] == 0
        # CrossRef should not have been called since no articles need resolution
        mock_instance._get.assert_not_called()


# ---------------------------------------------------------------------------
# Author normalization (#18) — OpenAlex, EuropePMC, Semantic Scholar
# ---------------------------------------------------------------------------

class TestAuthorNormalization:
    """Verify that all three API clients emit "Family Given" format, not "Given Family"."""

    def test_openalex_reverses_given_family_to_family_given(self):
        work = {
            "title": "Test paper",
            "authorships": [{"author": {"display_name": "Harriette Van Spall"}}],
            "cited_by_count": 0,
        }
        article = _parse_openalex_work(work)
        assert article is not None
        assert article.authors[0].startswith("Spall"), (
            f"Expected 'Spall ...' but got '{article.authors[0]}'"
        )

    def test_openalex_single_token_name_unchanged(self):
        work = {
            "title": "Test paper",
            "authorships": [{"author": {"display_name": "Plato"}}],
            "cited_by_count": 0,
        }
        article = _parse_openalex_work(work)
        assert article is not None
        assert article.authors[0] == "Plato"

    def test_openalex_two_token_name_reversed(self):
        work = {
            "title": "Test paper",
            "authorships": [{"author": {"display_name": "John Smith"}}],
            "cited_by_count": 0,
        }
        article = _parse_openalex_work(work)
        assert article is not None
        assert article.authors[0] == "Smith John"

    def test_openalex_cite_key_is_last_name(self):
        work = {
            "title": "Landmark study",
            "authorships": [{"author": {"display_name": "Cynthia Smith"}}],
            "cited_by_count": 0,
        }
        article = _parse_openalex_work(work)
        assert article is not None
        first_token = article.authors[0].split()[0]
        assert first_token == "Smith", f"cite key would be '{first_token}', expected 'Smith'"

    def test_europepmc_uses_last_first_when_available(self):
        result = {
            "title": "Test study",
            "authorList": {
                "author": [{"lastName": "Van Spall", "firstName": "Harriette G.C."}]
            },
            "pubYear": "2017",
        }
        article = _parse_europepmc_result(result)
        assert article is not None
        assert article.authors[0] == "Van Spall Harriette G.C."

    def test_europepmc_falls_back_to_fullname(self):
        result = {
            "title": "Test study",
            "authorList": {
                "author": [{"fullName": "Smith John"}]
            },
            "pubYear": "2020",
        }
        article = _parse_europepmc_result(result)
        assert article is not None
        assert article.authors[0] == "Smith John"

    def test_europepmc_cite_key_is_last_name(self):
        result = {
            "title": "Test study",
            "authorList": {
                "author": [{"lastName": "Harriette", "firstName": "G.C."}]
            },
            "pubYear": "2020",
        }
        article = _parse_europepmc_result(result)
        assert article is not None
        first_token = article.authors[0].split()[0]
        assert first_token == "Harriette"

    def test_semantic_scholar_reverses_given_family(self):
        paper = {
            "title": "Test paper",
            "externalIds": {},
            "authors": [{"name": "Mary Smith"}],
            "year": 2020,
        }
        article = _parse_s2_paper(paper)
        assert article is not None
        assert article.authors[0] == "Smith Mary"

    def test_semantic_scholar_cite_key_is_last_name(self):
        paper = {
            "title": "Test paper",
            "externalIds": {},
            "authors": [{"name": "Mary Ann Jones"}],
            "year": 2020,
        }
        article = _parse_s2_paper(paper)
        assert article is not None
        first_token = article.authors[0].split()[0]
        assert first_token == "Jones", f"cite key would be '{first_token}', expected 'Jones'"

    def test_semantic_scholar_single_name_unchanged(self):
        paper = {
            "title": "Test paper",
            "externalIds": {},
            "authors": [{"name": "Plato"}],
            "year": 400,
        }
        article = _parse_s2_paper(paper)
        assert article is not None
        assert article.authors[0] == "Plato"
