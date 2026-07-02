"""Unit tests for Phase 4 — Enrichment + Quality Appraisal skill modules.

All LLM and external API calls are mocked. No live network access.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import state as state_manager
from src.models.article import Article, EvidenceLevel
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType
from src.skills.data_extractor import (
    CSV_FIELDS,
    _apply_extraction,
    _format_article_text,
    _write_evidence_table,
)
from src.integrations.config import APIConfig
from src.skills.enricher import _build_s2_ids, _compute_velocity, _apply_s2_enrichment, enrich
from src.skills.quality_appraiser import detect_instrument, _apply_appraisal
from src.skills.evidence_classifier import (
    classify_level,
    _is_surrogate_outcome,
    classify,
)
from src.skills.citation_chaser import (
    _build_known_dois,
    _build_known_titles,
    _is_duplicate,
)


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


def _write_screened(review_dir: Path, articles: list[Article]) -> None:
    path = review_dir / "articles_screened.json"
    path.write_text(json.dumps([a.model_dump(mode="json") for a in articles], indent=2))


# ===========================================================================
# data_extractor — helpers
# ===========================================================================

class TestFormatArticleText:
    def test_includes_title(self):
        a = _make_article(title="A great study")
        text = _format_article_text(a)
        assert "A great study" in text

    def test_includes_abstract(self):
        a = _make_article(abstract="This study found X.")
        text = _format_article_text(a)
        assert "This study found X." in text

    def test_falls_back_to_tldr_when_no_abstract(self):
        a = _make_article(abstract=None, tldr="Short TLDR summary")
        text = _format_article_text(a)
        assert "Short TLDR summary" in text

    def test_includes_known_study_design(self):
        a = _make_article(study_design="RCT")
        text = _format_article_text(a)
        assert "RCT" in text

    def test_authors_truncated_to_three(self):
        a = _make_article(authors=["A B", "C D", "E F", "G H"])
        text = _format_article_text(a)
        assert "et al." in text
        assert "G H" not in text


class TestApplyExtraction:
    def test_sets_study_design(self):
        a = _make_article()
        _apply_extraction(a, {"study_design": "RCT"})
        assert a.study_design == "rct"

    def test_does_not_overwrite_existing_study_design(self):
        a = _make_article(study_design="Cohort")
        _apply_extraction(a, {"study_design": "RCT"})
        assert a.study_design == "Cohort"

    def test_sets_sample_size(self):
        a = _make_article()
        _apply_extraction(a, {"sample_size": 250})
        assert a.sample_size == 250

    def test_handles_string_sample_size(self):
        a = _make_article()
        _apply_extraction(a, {"sample_size": "450"})
        assert a.sample_size == 450

    def test_sets_primary_outcomes(self):
        a = _make_article()
        _apply_extraction(a, {"primary_outcomes": ["30-day readmission"]})
        assert a.primary_outcomes == ["30-day readmission"]

    def test_sets_coi_declared(self):
        a = _make_article()
        _apply_extraction(a, {"coi_declared": True})
        assert a.coi_declared is True

    def test_ignores_empty_dict(self):
        a = _make_article()
        _apply_extraction(a, {})
        assert a.study_design is None


class TestWriteEvidenceTable:
    def test_creates_csv_with_required_columns(self, tmp_path):
        articles = [_make_article(study_design="RCT")]
        _write_evidence_table(articles, tmp_path / "evidence_table.csv")
        with (tmp_path / "evidence_table.csv").open() as f:
            headers = csv.DictReader(f).fieldnames
        for field in CSV_FIELDS:
            assert field in headers

    def test_writes_one_row_per_article(self, tmp_path):
        articles = [_make_article(pmid=str(i)) for i in range(3)]
        _write_evidence_table(articles, tmp_path / "evidence_table.csv")
        with (tmp_path / "evidence_table.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 3

    def test_coi_declared_renders_as_yes_no(self, tmp_path):
        articles = [_make_article(coi_declared=True), _make_article(coi_declared=False)]
        _write_evidence_table(articles, tmp_path / "evidence_table.csv")
        with (tmp_path / "evidence_table.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["coi_declared"] == "Yes"
        assert rows[1]["coi_declared"] == "No"

    def test_empty_articles_produces_header_only(self, tmp_path):
        _write_evidence_table([], tmp_path / "evidence_table.csv")
        with (tmp_path / "evidence_table.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 0


# ===========================================================================
# enricher — helpers
# ===========================================================================

class TestBuildS2Ids:
    def test_prefers_pmid(self):
        a = _make_article(pmid="12345", doi="10.1001/test", s2_id="abc")
        ids = _build_s2_ids([a])
        assert ids[0] == "PMID:12345"

    def test_falls_back_to_doi(self):
        a = _make_article(pmid=None, doi="10.1001/test", s2_id="abc")
        ids = _build_s2_ids([a])
        assert ids[0] == "DOI:10.1001/test"

    def test_falls_back_to_s2_id(self):
        a = _make_article(pmid=None, doi=None, s2_id="abc123")
        ids = _build_s2_ids([a])
        assert ids[0] == "abc123"

    def test_none_when_no_ids(self):
        a = _make_article(pmid=None, doi=None, s2_id=None)
        ids = _build_s2_ids([a])
        assert ids[0] is None


class TestComputeVelocity:
    def test_computes_velocity(self):
        v = _compute_velocity(100, 2020)
        assert v is not None
        assert v > 0

    def test_returns_none_when_no_citations(self):
        assert _compute_velocity(None, 2020) is None

    def test_returns_none_when_no_year(self):
        assert _compute_velocity(100, None) is None

    def test_minimum_denominator_is_one(self):
        from datetime import datetime
        current_year = datetime.utcnow().year
        v = _compute_velocity(50, current_year)
        assert v == 50.0


class TestApplyS2Enrichment:
    def test_sets_citation_count(self):
        article = _make_article()
        enriched = _make_article(citation_count=75)
        _apply_s2_enrichment(article, enriched)
        assert article.citation_count == 75

    def test_sets_tldr_when_not_present(self):
        article = _make_article(tldr=None)
        enriched = _make_article(tldr="Short summary")
        _apply_s2_enrichment(article, enriched)
        assert article.tldr == "Short summary"

    def test_does_not_overwrite_existing_tldr(self):
        article = _make_article(tldr="Existing TLDR")
        enriched = _make_article(tldr="New TLDR")
        _apply_s2_enrichment(article, enriched)
        assert article.tldr == "Existing TLDR"

    def test_sets_oa_pdf_url_when_not_present(self):
        article = _make_article(oa_pdf_url=None)
        enriched = _make_article(oa_pdf_url="https://example.com/paper.pdf")
        _apply_s2_enrichment(article, enriched)
        assert article.oa_pdf_url == "https://example.com/paper.pdf"

    def test_computes_citation_velocity(self):
        article = _make_article(year=2020)
        enriched = _make_article(citation_count=60)
        _apply_s2_enrichment(article, enriched)
        assert article.citation_velocity is not None
        assert article.citation_velocity > 0


class TestEnrichIntegration:
    @pytest.mark.asyncio
    async def test_raises_when_wrong_status(self, tmp_path):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.fulltext_assessed)
        _write_included(review_dir, [_make_article()])
        with pytest.raises(ValueError, match="data_extracted"):
            await enrich(review_dir)

    @pytest.mark.asyncio
    async def test_empty_included_completes(self, tmp_path):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.data_extracted)
        _write_included(review_dir, [])
        with patch("src.skills.enricher.load_api_config", return_value=APIConfig()):
            result = await enrich(review_dir)
        assert result["enriched"] == 0
        assert state_manager.load(review_dir).status == ReviewStatus.enriched

    @pytest.mark.asyncio
    async def test_updates_status(self, tmp_path):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.data_extracted)
        _write_included(review_dir, [_make_article(pmid="1")])

        mock_s2 = AsyncMock()
        mock_s2.batch_lookup = AsyncMock(return_value=[])
        mock_oa = MagicMock()
        mock_oa.get_work = AsyncMock(return_value=None)

        with (
            patch("src.skills.enricher.load_api_config", return_value=APIConfig()),
            patch("src.skills.enricher.SemanticScholarClient", return_value=mock_s2),
            patch("src.skills.enricher.OpenAlexClient", return_value=mock_oa),
        ):
            result = await enrich(review_dir)

        assert result["enriched"] == 1
        assert state_manager.load(review_dir).status == ReviewStatus.enriched


# ===========================================================================
# quality_appraiser — helpers
# ===========================================================================

class TestDetectInstrument:
    def test_systematic_review_gets_amstar(self):
        assert detect_instrument("Systematic review") == "AMSTAR-2"

    def test_meta_analysis_gets_amstar(self):
        assert detect_instrument("Meta-analysis of RCTs") == "AMSTAR-2"

    def test_rct_gets_rob2(self):
        assert detect_instrument("Randomized controlled trial") == "RoB 2.0"

    def test_randomised_gets_rob2(self):
        assert detect_instrument("Randomised trial") == "RoB 2.0"

    def test_qualitative_gets_coreq(self):
        assert detect_instrument("Qualitative study with interviews") == "COREQ"

    def test_cohort_gets_strobe(self):
        assert detect_instrument("Prospective cohort study") == "STROBE"

    def test_case_control_gets_strobe(self):
        assert detect_instrument("Case-control study") == "STROBE"

    def test_none_defaults_to_strobe(self):
        assert detect_instrument(None) == "STROBE"

    def test_unknown_design_defaults_to_strobe(self):
        assert detect_instrument("Some unusual design") == "STROBE"


class TestApplyAppraisal:
    def test_sets_quality_score(self):
        a = _make_article()
        _apply_appraisal(a, "RoB 2.0", {"score": 0.75})
        assert a.quality_score == 0.75

    def test_clamps_score_to_zero_one(self):
        a = _make_article()
        _apply_appraisal(a, "STROBE", {"score": 1.5})
        assert a.quality_score == 1.0

    def test_sets_instrument(self):
        a = _make_article()
        _apply_appraisal(a, "AMSTAR-2", {"score": 0.5})
        assert a.quality_instrument == "AMSTAR-2"

    def test_sets_notes(self):
        a = _make_article()
        _apply_appraisal(a, "STROBE", {"score": 0.6, "notes": "Moderate quality"})
        assert a.quality_notes == "Moderate quality"

    def test_empty_data_does_not_crash(self):
        a = _make_article()
        _apply_appraisal(a, "RoB 2.0", {})
        assert a.quality_score is None




# ===========================================================================
# evidence_classifier — helpers
# ===========================================================================

class TestClassifyLevel:
    def test_systematic_review_is_level_I(self):
        assert classify_level("Systematic review and meta-analysis") == EvidenceLevel.level_I

    def test_meta_analysis_is_level_I(self):
        assert classify_level("Meta-analysis") == EvidenceLevel.level_I

    def test_rct_is_level_II(self):
        assert classify_level("Randomized controlled trial") == EvidenceLevel.level_II

    def test_rct_abbreviation_is_level_II(self):
        assert classify_level("RCT double-blind") == EvidenceLevel.level_II

    def test_pseudo_rct_is_level_III1(self):
        assert classify_level("Quasi-randomized controlled trial") == EvidenceLevel.level_III_1

    def test_interrupted_time_series_is_level_III1(self):
        assert classify_level("Interrupted time series") == EvidenceLevel.level_III_1

    def test_prospective_cohort_is_level_III2(self):
        assert classify_level("Prospective cohort study") == EvidenceLevel.level_III_2

    def test_retrospective_cohort_is_level_III3(self):
        assert classify_level("Retrospective cohort study") == EvidenceLevel.level_III_3

    def test_case_control_is_level_III3(self):
        assert classify_level("Case-control study") == EvidenceLevel.level_III_3

    def test_case_series_is_level_IV(self):
        assert classify_level("Case series") == EvidenceLevel.level_IV

    def test_expert_opinion_is_level_IV(self):
        assert classify_level("Expert opinion") == EvidenceLevel.level_IV

    def test_narrative_review_is_level_IV(self):
        assert classify_level("Narrative review") == EvidenceLevel.level_IV

    def test_none_returns_unknown(self):
        assert classify_level(None) == EvidenceLevel.unknown

    def test_unrecognized_returns_unknown(self):
        assert classify_level("Unknown design") == EvidenceLevel.unknown

    def test_case_insensitive(self):
        assert classify_level("RANDOMIZED CONTROLLED TRIAL") == EvidenceLevel.level_II


class TestIsSurrogateOutcome:
    def test_matching_outcome_returns_false(self):
        result = _is_surrogate_outcome(["30-day readmission rate"], ["30-day readmission rate"])
        assert result is False

    def test_partial_match_returns_false(self):
        result = _is_surrogate_outcome(["30-day all-cause readmission"], ["30-day readmission"])
        assert result is False

    def test_non_matching_returns_true(self):
        result = _is_surrogate_outcome(["6-minute walk distance"], ["30-day readmission"])
        assert result is True

    def test_empty_article_outcomes_returns_none(self):
        result = _is_surrogate_outcome([], ["30-day readmission"])
        assert result is None

    def test_empty_pico_outcomes_returns_none(self):
        result = _is_surrogate_outcome(["some outcome"], [])
        assert result is None


class TestClassifyIntegration:
    def test_raises_when_wrong_status(self, tmp_path):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article()])
        with pytest.raises(ValueError, match="quality_appraised"):
            classify(review_dir)

    def test_assigns_evidence_levels(self, tmp_path):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.quality_appraised)
        _write_included(review_dir, [
            _make_article(pmid="1", study_design="Systematic review"),
            _make_article(pmid="2", study_design="Randomized controlled trial"),
            _make_article(pmid="3", study_design="Cohort study"),
        ])
        result = classify(review_dir)
        assert result["classified"] == 3
        assert "I" in result["by_level"]
        assert "II" in result["by_level"]

    def test_flags_surrogate_outcomes(self, tmp_path):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.quality_appraised)
        _write_included(review_dir, [
            _make_article(pmid="1", study_design="RCT",
                          primary_outcomes=["6-minute walk distance"]),
        ])
        result = classify(review_dir)
        assert result["surrogate_flagged"] == 1

        articles = [Article(**a) for a in json.loads((review_dir / "articles_included.json").read_text())]
        assert articles[0].surrogate_outcome_flag is True

    def test_updates_status(self, tmp_path):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.quality_appraised)
        _write_included(review_dir, [_make_article(study_design="RCT")])
        classify(review_dir)
        assert state_manager.load(review_dir).status == ReviewStatus.evidence_classified

    def test_updates_articles_json(self, tmp_path):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.quality_appraised)
        _write_included(review_dir, [_make_article(study_design="Randomized controlled trial")])
        classify(review_dir)
        articles = [Article(**a) for a in json.loads((review_dir / "articles_included.json").read_text())]
        assert articles[0].evidence_level == EvidenceLevel.level_II


# ===========================================================================
# citation_chaser — helpers
# ===========================================================================

class TestBuildKnownDois:
    def test_collects_doi_set(self):
        articles = [_make_article(doi="10.1001/a"), _make_article(doi="10.1001/b")]
        known = _build_known_dois(articles)
        assert "10.1001/a" in known
        assert "10.1001/b" in known

    def test_lowercases_dois(self):
        articles = [_make_article(doi="10.1001/ABC")]
        known = _build_known_dois(articles)
        assert "10.1001/abc" in known

    def test_skips_articles_without_doi(self):
        articles = [_make_article(doi=None)]
        known = _build_known_dois(articles)
        assert len(known) == 0


class TestBuildKnownTitles:
    def test_collects_title_set(self):
        articles = [_make_article(title="A Study on CHF")]
        known = _build_known_titles(articles)
        assert "a study on chf" in known

    def test_strips_whitespace(self):
        articles = [_make_article(title="  Padded Title  ")]
        known = _build_known_titles(articles)
        assert "padded title" in known


class TestIsDuplicate:
    def test_doi_match_is_duplicate(self):
        candidate = _make_article(doi="10.1001/test")
        known_dois = {"10.1001/test"}
        known_titles: set[str] = set()
        assert _is_duplicate(candidate, known_dois, known_titles) is True

    def test_title_match_is_duplicate(self):
        candidate = _make_article(doi=None, title="Known Title")
        known_dois: set[str] = set()
        known_titles = {"known title"}
        assert _is_duplicate(candidate, known_dois, known_titles) is True

    def test_no_match_is_not_duplicate(self):
        candidate = _make_article(doi="10.1001/new", title="New Title")
        known_dois = {"10.1001/old"}
        known_titles = {"old title"}
        assert _is_duplicate(candidate, known_dois, known_titles) is False


