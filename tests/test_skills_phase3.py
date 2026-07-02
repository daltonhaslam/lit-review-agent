"""Unit tests for Phase 3 — Screening skill modules."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.abstract_screener import (
    _build_pico_context,
    _decision_from_str,
)
from src.skills.retrieval_list_generator import generate
from src.skills.fulltext_assessor import _match_pdfs, _normalize_doi_for_filename


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Adult patients hospitalized with heart failure",
        I="Care transitions interventions",
        C="Usual care",
        O=["30-day all-cause readmission rate", "all-cause mortality"],
        inclusion_criteria=["Adults ≥18", "Primary diagnosis of heart failure"],
        exclusion_criteria=["Pediatric population", "Non-English language"],
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
        screening_mode=ScreeningMode.thorough,
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


def _write_deduped(review_dir: Path, articles: list[Article]) -> None:
    path = review_dir / "articles_deduped.json"
    path.write_text(json.dumps([a.model_dump(mode="json") for a in articles], indent=2))


def _write_screened(review_dir: Path, articles: list[Article]) -> None:
    path = review_dir / "articles_screened.json"
    path.write_text(json.dumps([a.model_dump(mode="json") for a in articles], indent=2))


# ---------------------------------------------------------------------------
# abstract_screener — helper function unit tests
# ---------------------------------------------------------------------------

class TestBuildPicoContext:
    def test_includes_all_pico_fields(self, tmp_path):
        review, _ = _make_review(tmp_path)
        ctx = _build_pico_context(review)
        assert "P (Population):" in ctx
        assert "I (Intervention):" in ctx
        assert "C (Comparator):" in ctx
        assert "O (Outcomes):" in ctx
        assert "Inclusion criteria:" in ctx
        assert "Exclusion criteria:" in ctx

    def test_includes_study_design_when_set(self, tmp_path):
        review, _ = _make_review(tmp_path)
        review.pico = _make_pico(S="RCTs and cohort studies only")
        ctx = _build_pico_context(review)
        assert "S (Study design):" in ctx
        assert "RCTs and cohort studies only" in ctx

    def test_omits_study_design_when_none(self, tmp_path):
        review, _ = _make_review(tmp_path)
        review.pico = _make_pico(S=None)
        ctx = _build_pico_context(review)
        assert "S (Study design):" not in ctx


class TestDecisionFromStr:
    def test_valid_include(self):
        assert _decision_from_str("include") == ScreeningDecision.include

    def test_valid_exclude(self):
        assert _decision_from_str("exclude") == ScreeningDecision.exclude

    def test_valid_borderline(self):
        assert _decision_from_str("borderline") == ScreeningDecision.borderline

    def test_invalid_defaults_to_borderline(self):
        assert _decision_from_str("unclear") == ScreeningDecision.borderline

    def test_case_insensitive(self):
        assert _decision_from_str("Include") == ScreeningDecision.include



# ---------------------------------------------------------------------------
# retrieval_list_generator
# ---------------------------------------------------------------------------

class TestRetrievalListGenerator:
    def _make_screened_review(self, tmp_path) -> tuple[Review, Path]:
        return _make_review(tmp_path, status=ReviewStatus.citation_chased)

    def test_raises_when_wrong_status(self, tmp_path):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.deduplicated)
        _write_screened(review_dir, [_make_article()])
        with pytest.raises(ValueError, match="citation_chased"):
            generate(review_dir)

    def test_csv_written_with_correct_columns(self, tmp_path):
        _, review_dir = self._make_screened_review(tmp_path)
        articles = [
            _make_article(pmid="1", screening_decision=ScreeningDecision.include),
            _make_article(pmid="2", title="Another study", screening_decision=ScreeningDecision.borderline),
        ]
        _write_screened(review_dir, articles)

        generate(review_dir)

        csv_path = review_dir / "retrieval_list.csv"
        assert csv_path.exists()
        with csv_path.open() as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames
        assert "title" in headers
        assert "doi" in headers
        assert "pmid" in headers
        assert "oa_pdf_url" in headers
        assert "screening_decision" in headers

    def test_only_include_and_borderline_in_list(self, tmp_path):
        _, review_dir = self._make_screened_review(tmp_path)
        articles = [
            _make_article(pmid="1", title="Include this", screening_decision=ScreeningDecision.include),
            _make_article(pmid="2", title="Border case", screening_decision=ScreeningDecision.borderline),
            _make_article(pmid="3", title="Wrong population", screening_decision=ScreeningDecision.exclude),
        ]
        _write_screened(review_dir, articles)

        result = generate(review_dir)

        assert result["total"] == 2
        assert result["include"] == 1
        assert result["borderline"] == 1

    def test_excludes_not_in_csv(self, tmp_path):
        _, review_dir = self._make_screened_review(tmp_path)
        articles = [
            _make_article(pmid="1", title="Excluded", screening_decision=ScreeningDecision.exclude),
        ]
        _write_screened(review_dir, articles)

        generate(review_dir)

        csv_path = review_dir / "retrieval_list.csv"
        with csv_path.open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 0

    def test_open_access_count(self, tmp_path):
        _, review_dir = self._make_screened_review(tmp_path)
        articles = [
            _make_article(pmid="1", oa_status=True, oa_pdf_url="https://example.com/a.pdf",
                          screening_decision=ScreeningDecision.include),
            _make_article(pmid="2", oa_status=False, screening_decision=ScreeningDecision.include),
        ]
        _write_screened(review_dir, articles)

        result = generate(review_dir)
        assert result["open_access"] == 1

    def test_oa_url_in_csv(self, tmp_path):
        _, review_dir = self._make_screened_review(tmp_path)
        articles = [
            _make_article(pmid="1", oa_pdf_url="https://example.com/a.pdf",
                          screening_decision=ScreeningDecision.include),
        ]
        _write_screened(review_dir, articles)
        generate(review_dir)

        with (review_dir / "retrieval_list.csv").open() as f:
            row = next(csv.DictReader(f))
        assert row["oa_pdf_url"] == "https://example.com/a.pdf"

    def test_updates_status(self, tmp_path):
        _, review_dir = self._make_screened_review(tmp_path)
        _write_screened(review_dir, [_make_article(screening_decision=ScreeningDecision.include)])

        generate(review_dir)

        saved = state_manager.load(review_dir)
        assert saved.status == ReviewStatus.retrieval_list_generated

    def test_authors_truncated_with_et_al(self, tmp_path):
        _, review_dir = self._make_screened_review(tmp_path)
        articles = [
            _make_article(
                pmid="1",
                authors=["Smith J", "Jones A", "Brown K", "Lee S"],
                screening_decision=ScreeningDecision.include,
            )
        ]
        _write_screened(review_dir, articles)
        generate(review_dir)

        with (review_dir / "retrieval_list.csv").open() as f:
            row = next(csv.DictReader(f))
        assert "et al." in row["authors"]
        assert "Lee S" not in row["authors"]

    def test_includes_sorted_before_borderlines(self, tmp_path):
        _, review_dir = self._make_screened_review(tmp_path)
        articles = [
            _make_article(pmid="1", title="Borderline article", screening_decision=ScreeningDecision.borderline),
            _make_article(pmid="2", title="Include article", screening_decision=ScreeningDecision.include),
        ]
        _write_screened(review_dir, articles)
        generate(review_dir)

        with (review_dir / "retrieval_list.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["screening_decision"] == "include"
        assert rows[1]["screening_decision"] == "borderline"


# ---------------------------------------------------------------------------
# fulltext_assessor — helper unit tests
# ---------------------------------------------------------------------------

class TestNormalizeDoiForFilename:
    def test_replaces_slash(self):
        assert "/" not in _normalize_doi_for_filename("10.1001/test")

    def test_lowercases(self):
        assert _normalize_doi_for_filename("10.1001/TEST") == "10.1001_test"

    def test_replaces_colon(self):
        result = _normalize_doi_for_filename("10.1001:test")
        assert ":" not in result


class TestMatchPdfs:
    def test_match_by_pmid(self, tmp_path):
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        (pdf_dir / "12345678.pdf").write_bytes(b"%PDF")
        articles = [_make_article(pmid="12345678")]
        matched = _match_pdfs(articles, pdf_dir)
        assert 0 in matched
        assert matched[0].name == "12345678.pdf"

    def test_match_by_doi(self, tmp_path):
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        doi = "10.1001/test"
        safe_name = _normalize_doi_for_filename(doi)
        (pdf_dir / f"{safe_name}.pdf").write_bytes(b"%PDF")
        articles = [_make_article(doi=doi)]
        matched = _match_pdfs(articles, pdf_dir)
        assert 0 in matched

    def test_no_match_when_pdf_dir_missing(self, tmp_path):
        pdf_dir = tmp_path / "pdfs"  # does not exist
        articles = [_make_article(pmid="99999")]
        matched = _match_pdfs(articles, pdf_dir)
        assert matched == {}

    def test_no_match_when_pdf_not_present(self, tmp_path):
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        articles = [_make_article(pmid="99999")]
        matched = _match_pdfs(articles, pdf_dir)
        assert matched == {}

    def test_pmid_takes_priority_over_doi(self, tmp_path):
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        (pdf_dir / "12345.pdf").write_bytes(b"%PDF")
        doi_safe = _normalize_doi_for_filename("10.1001/test")
        (pdf_dir / f"{doi_safe}.pdf").write_bytes(b"%PDF")
        articles = [_make_article(pmid="12345", doi="10.1001/test")]
        matched = _match_pdfs(articles, pdf_dir)
        assert matched[0].name == "12345.pdf"

