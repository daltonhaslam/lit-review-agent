"""Unit tests for Phase 5 — Synthesis + Output skill modules.

Pure-function tests only. LLM integration tests moved to test_skills_phase_c5.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.agent import state as state_manager
from src.models.article import Article, EvidenceLevel, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import PRISMACounts, Review, ReviewStatus, ReviewType

from src.skills.gap_analyzer import (
    _build_article_summary,
    _build_evidence_map,
    _evidence_level_distribution,
    _study_design_distribution,
    _recency_stats,
    _write_gap_analysis,
)
from src.skills.synthesizer import (
    _author_year,
    _build_evidence_map as synth_evidence_map,
    _build_quantitative_scaffold,
    _build_synthesis_context,
)
from src.skills.output_formatter import (
    _bib_key,
    _write_bibtex,
    _write_prisma_flow,
    _write_ris,
    _stub_search_log,
    _stub_retrieval_list,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Adult inpatients with heart failure",
        I="Care transitions interventions",
        C="Usual care",
        O=["30-day all-cause readmission", "all-cause mortality"],
        inclusion_criteria=["Adults ≥18", "Primary HF diagnosis"],
        exclusion_criteria=["Pediatric"],
    )
    defaults.update(kwargs)
    return PICO(**defaults)


def _make_review(tmp_path: Path, status: ReviewStatus = ReviewStatus.citation_chased, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-hf-2026-04"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="test-hf-2026-04",
        raw_question="Do care transitions reduce HF readmission?",
        review_type=ReviewType.systematic,
        pico=_make_pico(),
        status=status,
        sources_searched=["pubmed", "openalex"],
        prisma_counts=PRISMACounts(
            identified=200,
            screened=180,
            eligibility=30,
            included=15,
            citation_chasing_added=2,
            excluded_screening={"not relevant": 120, "wrong population": 60},
            excluded_fulltext={"no intervention details": 10, "wrong outcomes": 5},
        ),
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
        authors=["Smith J", "Jones A", "Brown K"],
        year=2022,
        journal="NEJM",
        source_dbs=["pubmed"],
        study_design="RCT",
        evidence_level=EvidenceLevel.level_II,
        quality_score=0.82,
        quality_instrument="RoB 2.0",
        sample_size=350,
        primary_outcomes=["30-day all-cause readmission"],
        effect_sizes="OR 0.74 (95% CI 0.62–0.88), p=0.001",
        author_conclusions="Care transitions interventions significantly reduced 30-day readmission.",
        screening_decision=ScreeningDecision.include,
    )
    defaults.update(kwargs)
    return Article(**defaults)


def _write_included(review_dir: Path, articles: list[Article]) -> None:
    path = review_dir / "articles_included.json"
    path.write_text(json.dumps([a.model_dump(mode="json") for a in articles], indent=2))


# ===========================================================================
# gap_analyzer — helpers
# ===========================================================================

class TestBuildArticleSummary:
    def test_includes_title(self):
        articles = [_make_article(title="My RCT study")]
        summary = _build_article_summary(articles)
        assert "My RCT study" in summary

    def test_includes_evidence_level(self):
        articles = [_make_article(evidence_level=EvidenceLevel.level_I)]
        summary = _build_article_summary(articles)
        assert "I" in summary

    def test_surrogate_flag_included(self):
        articles = [_make_article(surrogate_outcome_flag=True)]
        summary = _build_article_summary(articles)
        assert "SURROGATE" in summary

    def test_no_surrogate_flag_by_default(self):
        articles = [_make_article(surrogate_outcome_flag=False)]
        summary = _build_article_summary(articles)
        assert "SURROGATE" not in summary

    def test_multiple_articles_numbered(self):
        articles = [_make_article(title=f"Study {i}") for i in range(3)]
        summary = _build_article_summary(articles)
        assert "1." in summary
        assert "2." in summary
        assert "3." in summary

    def test_shows_sample_size(self):
        articles = [_make_article(sample_size=500)]
        summary = _build_article_summary(articles)
        assert "N=500" in summary

    def test_handles_no_sample_size(self):
        articles = [_make_article(sample_size=None)]
        summary = _build_article_summary(articles)
        assert "N=" not in summary

    def test_shows_primary_outcomes(self):
        articles = [_make_article(primary_outcomes=["30-day readmission"])]
        summary = _build_article_summary(articles)
        assert "30-day readmission" in summary

    def test_empty_list_returns_empty_string(self):
        assert _build_article_summary([]) == ""


class TestBuildEvidenceMap:
    def test_returns_dict_per_outcome(self):
        articles = [_make_article(primary_outcomes=["readmission"], evidence_level=EvidenceLevel.level_II)]
        result = _build_evidence_map(articles)
        assert "readmission" in result

    def test_counts_by_design_bucket(self):
        articles = [
            _make_article(primary_outcomes=["readmission"], evidence_level=EvidenceLevel.level_II),
            _make_article(primary_outcomes=["readmission"], evidence_level=EvidenceLevel.level_II),
        ]
        result = _build_evidence_map(articles)
        assert result["readmission"].get("RCT", 0) == 2

    def test_empty_articles_returns_empty(self):
        assert _build_evidence_map([]) == {}

    def test_multiple_outcomes_tracked_separately(self):
        a = _make_article(primary_outcomes=["readmission", "mortality"], evidence_level=EvidenceLevel.level_I)
        result = _build_evidence_map([a])
        assert "readmission" in result
        assert "mortality" in result


class TestRecencyStats:
    def test_identifies_oldest_and_newest(self):
        articles = [_make_article(year=y) for y in [2018, 2021, 2023]]
        stats = _recency_stats(articles)
        assert stats["oldest"] == 2018
        assert stats["newest"] == 2023

    def test_recent_count_correct(self):
        articles = [_make_article(year=2015), _make_article(year=2024)]
        stats = _recency_stats(articles)
        assert stats["recent_count"] == 1
        assert stats["total"] == 2

    def test_handles_no_years(self):
        articles = [_make_article(year=None)]
        stats = _recency_stats(articles)
        assert stats["oldest"] is None
        assert stats["newest"] is None

    def test_all_recent(self):
        articles = [_make_article(year=2024), _make_article(year=2025)]
        stats = _recency_stats(articles)
        assert stats["recent_count"] == 2


class TestEvidenceLevelDistribution:
    def test_counts_by_level(self):
        articles = [
            _make_article(evidence_level=EvidenceLevel.level_II),
            _make_article(evidence_level=EvidenceLevel.level_II),
            _make_article(evidence_level=EvidenceLevel.level_I),
        ]
        dist = _evidence_level_distribution(articles)
        assert dist["II"] == 2
        assert dist["I"] == 1

    def test_unknown_level(self):
        articles = [_make_article(evidence_level=None)]
        dist = _evidence_level_distribution(articles)
        assert dist["unknown"] == 1

    def test_empty_list(self):
        assert _evidence_level_distribution([]) == {}


class TestWriteGapAnalysis:
    def test_creates_file(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        articles = [_make_article()]
        gap_data = {
            "pico_gaps": [{"aspect": "O", "gap": "Mortality not addressed", "severity": "high"}],
            "outcome_gaps": ["long-term mortality"],
            "population_gaps": ["elderly >80"],
            "recency_gaps": "Most articles >5 years old.",
            "design_gaps": ["No RCTs for subgroup analysis"],
            "suggested_queries": [{"target_gap": "mortality", "query": '"mortality"[tiab]', "rationale": "covers gap"}],
            "recommendation": "iterate",
            "recommendation_rationale": "Mortality outcome not adequately covered.",
        }
        _write_gap_analysis(
            review, articles, gap_data, review_dir / "gap_analysis.md",
            recency=_recency_stats(articles),
            evidence_level_dist=_evidence_level_distribution(articles),
            study_design_dist=_study_design_distribution(articles),
            evidence_map=_build_evidence_map(articles),
        )
        assert (review_dir / "gap_analysis.md").exists()

    def test_contains_recommendation(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        articles = [_make_article()]
        gap_data = {"recommendation": "stop", "recommendation_rationale": "Coverage is adequate."}
        _write_gap_analysis(
            review, articles, gap_data, review_dir / "gap_analysis.md",
            recency=_recency_stats(articles),
            evidence_level_dist=_evidence_level_distribution(articles),
            study_design_dist=_study_design_distribution(articles),
            evidence_map=_build_evidence_map(articles),
        )
        text = (review_dir / "gap_analysis.md").read_text()
        assert "STOP" in text
        assert "Coverage is adequate." in text

    def test_contains_evidence_map_header(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        articles = [_make_article()]
        gap_data = {"recommendation": "stop", "recommendation_rationale": "Fine."}
        _write_gap_analysis(
            review, articles, gap_data, review_dir / "gap_analysis.md",
            recency=_recency_stats(articles),
            evidence_level_dist=_evidence_level_distribution(articles),
            study_design_dist=_study_design_distribution(articles),
            evidence_map=_build_evidence_map(articles),
        )
        text = (review_dir / "gap_analysis.md").read_text()
        assert "Evidence Map" in text

    def test_pico_gaps_shown(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        articles = [_make_article()]
        gap_data = {
            "pico_gaps": [{"aspect": "P", "gap": "Elderly excluded", "severity": "medium"}],
            "recommendation": "stop",
            "recommendation_rationale": "OK.",
        }
        _write_gap_analysis(
            review, articles, gap_data, review_dir / "gap_analysis.md",
            recency=_recency_stats(articles),
            evidence_level_dist=_evidence_level_distribution(articles),
            study_design_dist=_study_design_distribution(articles),
            evidence_map=_build_evidence_map(articles),
        )
        text = (review_dir / "gap_analysis.md").read_text()
        assert "Elderly excluded" in text

    def test_suggested_queries_shown(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        articles = [_make_article()]
        gap_data = {
            "suggested_queries": [{"target_gap": "mortality", "query": '"mortality"[tiab]', "rationale": "gap"}],
            "recommendation": "iterate",
            "recommendation_rationale": "Add mortality.",
        }
        _write_gap_analysis(
            review, articles, gap_data, review_dir / "gap_analysis.md",
            recency=_recency_stats(articles),
            evidence_level_dist=_evidence_level_distribution(articles),
            study_design_dist=_study_design_distribution(articles),
            evidence_map=_build_evidence_map(articles),
        )
        text = (review_dir / "gap_analysis.md").read_text()
        assert '"mortality"[tiab]' in text

    def test_no_gap_data_does_not_crash(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        articles = [_make_article()]
        gap_data = {"recommendation": "stop", "recommendation_rationale": "Fine."}
        _write_gap_analysis(
            review, articles, gap_data, review_dir / "gap_analysis.md",
            recency=_recency_stats(articles),
            evidence_level_dist=_evidence_level_distribution(articles),
            study_design_dist=_study_design_distribution(articles),
            evidence_map=_build_evidence_map(articles),
        )
        assert (review_dir / "gap_analysis.md").exists()


# ===========================================================================
# synthesizer — helpers
# ===========================================================================

class TestAuthorYear:
    def test_family_name_and_year(self):
        a = _make_article(authors=["Smith J"], year=2022)
        assert _author_year(a) == "Smith 2022"

    def test_uses_first_word_as_family_name(self):
        a = _make_article(authors=["Smith Robert John"], year=2021)
        assert _author_year(a) == "Smith 2021"

    def test_no_authors_uses_unknown(self):
        a = _make_article(authors=[], year=2022)
        assert _author_year(a) == "Unknown 2022"

    def test_no_year_uses_question_mark(self):
        a = _make_article(year=None)
        assert "?" in _author_year(a)


class TestBuildSynthesisContext:
    def test_includes_title(self):
        articles = [_make_article(title="My Unique RCT Title")]
        ctx = _build_synthesis_context(articles)
        assert "My Unique RCT Title" in ctx

    def test_includes_effect_sizes(self):
        articles = [_make_article(effect_sizes="OR 0.74 (95% CI 0.62–0.88)")]
        ctx = _build_synthesis_context(articles)
        assert "OR 0.74" in ctx

    def test_surrogate_flag_shown(self):
        articles = [_make_article(surrogate_outcome_flag=True)]
        ctx = _build_synthesis_context(articles)
        assert "SURROGATE" in ctx

    def test_multiple_articles_separated(self):
        articles = [_make_article(title=f"Study {i}") for i in range(3)]
        ctx = _build_synthesis_context(articles)
        assert ctx.count("[1]") == 1
        assert ctx.count("[2]") == 1
        assert ctx.count("[3]") == 1

    def test_no_effect_sizes_shows_not_reported(self):
        articles = [_make_article(effect_sizes=None)]
        ctx = _build_synthesis_context(articles)
        assert "not reported" in ctx


class TestSynthEvidenceMap:
    def test_returns_list_of_outcome_rows(self):
        articles = [_make_article(
            primary_outcomes=["30-day all-cause readmission"],
            evidence_level=EvidenceLevel.level_II,
        )]
        pico_outcomes = ["30-day all-cause readmission"]
        rows = synth_evidence_map(articles, pico_outcomes)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "30-day all-cause readmission"

    def test_counts_articles_by_outcome(self):
        articles = [
            _make_article(primary_outcomes=["readmission"], evidence_level=EvidenceLevel.level_II),
            _make_article(primary_outcomes=["readmission"], evidence_level=EvidenceLevel.level_I),
        ]
        rows = synth_evidence_map(articles, ["readmission"])
        assert rows[0]["total"] == 2

    def test_empty_outcomes_list(self):
        articles = [_make_article()]
        rows = synth_evidence_map(articles, [])
        assert rows == []

    def test_outcome_row_total_equals_included_count(self):
        articles = [_make_article(primary_outcomes=["readmission"])]
        rows = synth_evidence_map(articles, ["mortality"])
        assert rows[0]["total"] == len(articles)


class TestBuildQuantitativeScaffold:
    def test_includes_articles_with_effect_sizes(self):
        a = _make_article(effect_sizes="OR 0.74 (95% CI 0.62–0.88)")
        rows = _build_quantitative_scaffold([a])
        assert len(rows) == 1
        assert "OR 0.74" in rows[0]["effect_sizes"]

    def test_excludes_articles_without_effect_sizes(self):
        a = _make_article(effect_sizes=None)
        rows = _build_quantitative_scaffold([a])
        assert len(rows) == 0

    def test_includes_evidence_level(self):
        a = _make_article(effect_sizes="p=0.001", evidence_level=EvidenceLevel.level_II)
        rows = _build_quantitative_scaffold([a])
        assert rows[0]["evidence_level"] == "II"

    def test_surrogate_flag_preserved(self):
        a = _make_article(effect_sizes="OR 1.0", surrogate_outcome_flag=True)
        rows = _build_quantitative_scaffold([a])
        assert rows[0]["surrogate"] is True

    def test_empty_list(self):
        assert _build_quantitative_scaffold([]) == []


# ===========================================================================
# output_formatter — helpers
# ===========================================================================

class TestBibKey:
    def test_builds_key_from_author_year_title(self):
        a = _make_article(authors=["Smith J"], year=2022, title="Heart Failure Readmission Study")
        key = _bib_key(a, 1)
        assert "smith" in key
        assert "2022" in key

    def test_no_author_falls_back_to_index(self):
        a = _make_article(authors=[], year=None, title="A")
        key = _bib_key(a, 5)
        assert key == "article5"

    def test_special_characters_stripped(self):
        a = _make_article(authors=["O'Brien M"], year=2020, title="Study of care")
        key = _bib_key(a, 1)
        assert re.match(r"^[a-z0-9]+$", key)

    def test_different_articles_different_keys(self):
        a1 = _make_article(authors=["Smith J"], year=2022, title="Alpha study")
        a2 = _make_article(authors=["Jones K"], year=2021, title="Beta study")
        assert _bib_key(a1, 1) != _bib_key(a2, 2)


class TestWriteRIS:
    def test_creates_file(self, tmp_path):
        articles = [_make_article()]
        _write_ris(articles, tmp_path / "citations.ris")
        assert (tmp_path / "citations.ris").exists()

    def test_contains_ris_type_tag(self, tmp_path):
        articles = [_make_article()]
        _write_ris(articles, tmp_path / "citations.ris")
        text = (tmp_path / "citations.ris").read_text()
        assert "TY  -" in text

    def test_contains_title(self, tmp_path):
        articles = [_make_article(title="My important RCT")]
        _write_ris(articles, tmp_path / "citations.ris")
        text = (tmp_path / "citations.ris").read_text()
        assert "My important RCT" in text

    def test_contains_end_record(self, tmp_path):
        articles = [_make_article()]
        _write_ris(articles, tmp_path / "citations.ris")
        text = (tmp_path / "citations.ris").read_text()
        assert "ER  -" in text

    def test_contains_doi(self, tmp_path):
        articles = [_make_article(doi="10.1001/test.123")]
        _write_ris(articles, tmp_path / "citations.ris")
        text = (tmp_path / "citations.ris").read_text()
        assert "10.1001/test.123" in text

    def test_multiple_articles_all_present(self, tmp_path):
        articles = [_make_article(pmid=str(i), title=f"Study {i}") for i in range(3)]
        _write_ris(articles, tmp_path / "citations.ris")
        text = (tmp_path / "citations.ris").read_text()
        for i in range(3):
            assert f"Study {i}" in text

    def test_empty_articles(self, tmp_path):
        _write_ris([], tmp_path / "citations.ris")
        assert (tmp_path / "citations.ris").exists()


class TestWriteBibTeX:
    def test_creates_file(self, tmp_path):
        articles = [_make_article()]
        _write_bibtex(articles, tmp_path / "citations.bib")
        assert (tmp_path / "citations.bib").exists()

    def test_contains_article_type(self, tmp_path):
        articles = [_make_article()]
        _write_bibtex(articles, tmp_path / "citations.bib")
        text = (tmp_path / "citations.bib").read_text()
        assert "@article{" in text

    def test_contains_title_field(self, tmp_path):
        articles = [_make_article(title="My RCT on heart failure")]
        _write_bibtex(articles, tmp_path / "citations.bib")
        text = (tmp_path / "citations.bib").read_text()
        assert "My RCT on heart failure" in text

    def test_contains_year_field(self, tmp_path):
        articles = [_make_article(year=2022)]
        _write_bibtex(articles, tmp_path / "citations.bib")
        text = (tmp_path / "citations.bib").read_text()
        assert "year = {2022}" in text

    def test_doi_included(self, tmp_path):
        articles = [_make_article(doi="10.1001/jama.2022.001")]
        _write_bibtex(articles, tmp_path / "citations.bib")
        text = (tmp_path / "citations.bib").read_text()
        assert "10.1001/jama.2022.001" in text

    def test_multiple_articles(self, tmp_path):
        articles = [_make_article(pmid=str(i)) for i in range(4)]
        _write_bibtex(articles, tmp_path / "citations.bib")
        text = (tmp_path / "citations.bib").read_text()
        assert text.count("@article{") == 4


class TestWritePrismaFlow:
    def test_creates_file(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        _write_prisma_flow(review, review_dir / "prisma_flow.md", [])
        assert (review_dir / "prisma_flow.md").exists()

    def test_contains_identified_count(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        _write_prisma_flow(review, review_dir / "prisma_flow.md", [])
        text = (review_dir / "prisma_flow.md").read_text()
        assert "200" in text

    def test_contains_included_count(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        articles = [_make_article(pmid=str(i)) for i in range(15)]
        _write_prisma_flow(review, review_dir / "prisma_flow.md", articles)
        text = (review_dir / "prisma_flow.md").read_text()
        assert "Total studies included in review: 15" in text

    def test_total_included_not_double_counted(self, tmp_path):
        # Current pipeline runs citation chasing at Stage 8.5 (before full-text), so
        # prisma_counts.included ALREADY contains the CC survivors. The published total
        # must equal the real included-set size, not included + citation_chasing_added.
        review, review_dir = _make_review(
            tmp_path,
            prisma_counts=PRISMACounts(
                identified=200,
                screened=180,
                eligibility=12,
                included=10,
                citation_chasing_added=8,  # abstract-level: 8 CC articles advanced to full-text
                excluded_screening={"not relevant": 120},
                excluded_fulltext={"wrong outcomes": 2},
            ),
        )
        # 10 included articles, 3 of which came via citation chasing and survived full-text.
        articles = [_make_article(pmid=str(i)) for i in range(7)] + [
            _make_article(pmid=str(i), citation_chased=True) for i in range(7, 10)
        ]
        _write_prisma_flow(review, review_dir / "prisma_flow.md", articles)
        text = (review_dir / "prisma_flow.md").read_text()
        assert "Total studies included in review: 10" in text
        assert "Total studies included in review: 18" not in text  # the double-count
        # Internal split must reconcile: 7 primary + 3 citation-chased = 10 total.
        assert "Studies included from database searching: **7**" in text
        assert "Studies added via citation searching: **3**" in text

    def test_iteration_included_shown_as_separate_stream(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        articles = (
            [_make_article(pmid=str(i)) for i in range(5)]
            + [_make_article(pmid=str(i), citation_chased=True) for i in range(5, 7)]
            + [_make_article(pmid=str(i), iteration_added=True) for i in range(7, 9)]
        )
        _write_prisma_flow(review, review_dir / "prisma_flow.md", articles)
        text = (review_dir / "prisma_flow.md").read_text()
        assert "Studies added via iteration searching: **2**" in text
        assert "Studies included from database searching: **5**" in text  # 9 - 2 cc - 2 iter
        assert "Total studies included in review: 9" in text

    def test_no_iteration_line_when_no_iteration_includes(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        _write_prisma_flow(review, review_dir / "prisma_flow.md", [_make_article(pmid="1")])
        assert "iteration searching" not in (review_dir / "prisma_flow.md").read_text()

    def test_contains_exclusion_reasons(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        _write_prisma_flow(review, review_dir / "prisma_flow.md", [])
        text = (review_dir / "prisma_flow.md").read_text()
        assert "not relevant" in text


class TestStubWriters:
    def test_stub_search_log_creates_if_missing(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        _stub_search_log(review_dir)
        assert (review_dir / "search_log.md").exists()

    def test_stub_search_log_does_not_overwrite_existing(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        (review_dir / "search_log.md").write_text("existing content")
        _stub_search_log(review_dir)
        assert (review_dir / "search_log.md").read_text() == "existing content"

    def test_stub_retrieval_list_creates_if_missing(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        _stub_retrieval_list(review_dir)
        assert (review_dir / "retrieval_list.csv").exists()

    def test_stub_retrieval_list_does_not_overwrite_existing(self, tmp_path):
        review, review_dir = _make_review(tmp_path)
        (review_dir / "retrieval_list.csv").write_text("title,doi\nMyStudy,10.1/x")
        _stub_retrieval_list(review_dir)
        assert "MyStudy" in (review_dir / "retrieval_list.csv").read_text()


def test_study_design_distribution_counts_by_design():
    from src.models.article import Article, ScreeningDecision
    arts = [
        Article(pmid="1", title="A", study_design="rct", screening_decision=ScreeningDecision.include),
        Article(pmid="2", title="B", study_design="rct", screening_decision=ScreeningDecision.include),
        Article(pmid="3", title="C", study_design="retrospective_cohort", screening_decision=ScreeningDecision.include),
        Article(pmid="4", title="D", study_design=None, screening_decision=ScreeningDecision.include),
    ]
    dist = _study_design_distribution(arts)
    assert dist["rct"] == 2
    assert dist["retrospective_cohort"] == 1
    assert dist.get("unknown", 0) == 1


def test_evidence_level_distribution_renamed_function_works():
    from src.models.article import Article, EvidenceLevel, ScreeningDecision
    arts = [
        Article(pmid="1", title="A", evidence_level=EvidenceLevel.level_IV, screening_decision=ScreeningDecision.include),
    ]
    dist = _evidence_level_distribution(arts)
    assert dist["IV"] == 1


