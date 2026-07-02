"""Unit tests for Phase C4 — subscription conversion of quality_appraiser, citation_chaser,
gap_analyzer, and iteration_manager.

Tests the --prepare / --save (and --retrieve / --merge) CLI patterns for all four skills.
No LLM or Anthropic API calls. External API calls (CrossRef, S2, PubMed) are mocked where needed.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import state as state_manager
from src.models.article import Article, EvidenceLevel, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import PRISMACounts, Review, ReviewStatus, ReviewType, ScreeningMode
from src.integrations.config import APIConfig
from src.skills.quality_appraiser import prepare as qa_prepare, save as qa_save
from src.skills.citation_chaser import (
    save as cc_save,
    _build_known_dois,
    _build_known_titles,
    _is_duplicate,
    _CANDIDATES_FILE as CC_CANDIDATES_FILE,
)
from src.skills.gap_analyzer import prepare as ga_prepare, save as ga_save
from src.skills.iteration_manager import (
    merge as im_merge,
    _CANDIDATES_FILE as IM_CANDIDATES_FILE,
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
    (review_dir / "articles_included.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _write_screened(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_screened.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _capture_qa_prepare(capsys, review_dir) -> dict:
    qa_prepare(review_dir)
    return json.loads(capsys.readouterr().out)


def _capture_qa_save(capsys, review_dir, appraisals: list[dict]) -> dict:
    qa_save(review_dir, json.dumps({"appraisals": appraisals}))
    return json.loads(capsys.readouterr().out)


def _capture_ga_prepare(capsys, review_dir) -> dict:
    ga_prepare(review_dir)
    return json.loads(capsys.readouterr().out)


def _capture_ga_save(capsys, review_dir, gap_data: dict) -> dict:
    ga_save(review_dir, json.dumps(gap_data))
    return json.loads(capsys.readouterr().out)


def _capture_im_merge(capsys, review_dir, decisions: list[dict]) -> dict:
    im_merge(review_dir, json.dumps({"decisions": decisions}))
    return json.loads(capsys.readouterr().out)


def _minimal_gap_data(**kwargs) -> dict:
    base = {
        "pico_gaps": [],
        "outcome_gaps": [],
        "population_gaps": [],
        "recency_gaps": None,
        "design_gaps": [],
        "suggested_queries": [{"target_gap": "mortality", "query": "heart failure mortality[ti]", "rationale": "Gap"}],
        "recommendation": "stop",
        "recommendation_rationale": "Adequate coverage.",
    }
    base.update(kwargs)
    return base


# ===========================================================================
# quality_appraiser — prepare
# ===========================================================================

class TestQualityAppraiserPrepare:
    def test_wrong_status_dies(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.data_extracted)
        _write_included(review_dir, [_make_article()])
        with pytest.raises(SystemExit):
            qa_prepare(review_dir)
        assert "error" in json.loads(capsys.readouterr().out)

    def test_outputs_all_articles(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(3)])
        out = _capture_qa_prepare(capsys, review_dir)
        assert out["total_articles"] == 3
        assert len(out["articles"]) == 3

    def test_article_has_orig_idx(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(pmid="1"), _make_article(pmid="2")])
        out = _capture_qa_prepare(capsys, review_dir)
        assert out["articles"][0]["orig_idx"] == 0
        assert out["articles"][1]["orig_idx"] == 1

    def test_instrument_detected_correctly(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [
            _make_article(pmid="1", study_design="Randomized controlled trial"),
            _make_article(pmid="2", study_design="Cohort study"),
        ])
        out = _capture_qa_prepare(capsys, review_dir)
        instruments = {a["orig_idx"]: a["instrument"] for a in out["articles"]}
        assert instruments[0] == "RoB 2.0"
        assert instruments[1] == "STROBE"

    def test_article_text_present(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(title="Landmark CHF trial")])
        out = _capture_qa_prepare(capsys, review_dir)
        assert "Landmark CHF trial" in out["articles"][0]["article_text"]

    def test_appraisal_prompt_present(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article(study_design="RCT")])
        out = _capture_qa_prepare(capsys, review_dir)
        assert "appraisal_prompt" in out["articles"][0]
        assert "RoB 2.0" in out["articles"][0]["appraisal_prompt"]

    def test_appraisal_task_in_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [_make_article()])
        out = _capture_qa_prepare(capsys, review_dir)
        assert "appraisal_task" in out

    def test_empty_included_outputs_zero(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        _write_included(review_dir, [])
        out = _capture_qa_prepare(capsys, review_dir)
        assert out["total_articles"] == 0
        assert out["articles"] == []


# ===========================================================================
# quality_appraiser — save
# ===========================================================================

class TestQualityAppraiserSave:
    def _setup(self, tmp_path, articles=None):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.enriched)
        if articles is None:
            articles = [_make_article(pmid="1", study_design="RCT")]
        _write_included(review_dir, articles)
        return review_dir

    def test_applies_score_to_article(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1", study_design="RCT")])
        _capture_qa_save(capsys, review_dir, [{"orig_idx": 0, "score": 0.8, "notes": "Good quality"}])
        articles = json.loads((review_dir / "articles_included.json").read_text())
        assert articles[0]["quality_score"] == pytest.approx(0.8)

    def test_applies_notes_to_article(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1", study_design="RCT")])
        _capture_qa_save(capsys, review_dir, [{"orig_idx": 0, "score": 0.7, "notes": "Some concerns"}])
        articles = json.loads((review_dir / "articles_included.json").read_text())
        assert articles[0]["quality_notes"] == "Some concerns"

    def test_instrument_stored_on_article(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1", study_design="Systematic review")])
        _capture_qa_save(capsys, review_dir, [{"orig_idx": 0, "score": 0.75, "notes": "Moderate"}])
        articles = json.loads((review_dir / "articles_included.json").read_text())
        assert articles[0]["quality_instrument"] == "AMSTAR-2"

    def test_partial_appraisals_ok(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1"), _make_article(pmid="2")])
        _capture_qa_save(capsys, review_dir, [{"orig_idx": 0, "score": 0.6, "notes": "OK"}])
        articles = json.loads((review_dir / "articles_included.json").read_text())
        assert articles[0]["quality_score"] == pytest.approx(0.6)
        assert articles[1]["quality_score"] is None

    def test_status_updated_to_quality_appraised(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_qa_save(capsys, review_dir, [])
        assert state_manager.load(review_dir).status == ReviewStatus.quality_appraised

    def test_quality_report_written(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1", study_design="RCT")])
        _capture_qa_save(capsys, review_dir, [{"orig_idx": 0, "score": 0.9, "notes": "High quality"}])
        assert (review_dir / "quality_report.md").exists()

    def test_by_instrument_in_output(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [
            _make_article(pmid="1", study_design="Systematic review"),
            _make_article(pmid="2", study_design="RCT"),
        ])
        out = _capture_qa_save(capsys, review_dir, [])
        assert "AMSTAR-2" in out["by_instrument"]
        assert "RoB 2.0" in out["by_instrument"]

    def test_score_clamped_to_zero_one(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid="1", study_design="RCT")])
        _capture_qa_save(capsys, review_dir, [{"orig_idx": 0, "score": 1.5, "notes": ""}])
        articles = json.loads((review_dir / "articles_included.json").read_text())
        assert articles[0]["quality_score"] == pytest.approx(1.0)


# ===========================================================================
# citation_chaser — save (prepare is async/API-dependent; tested via mock separately)
# ===========================================================================

class TestCitationChaserSave:
    def _write_candidates(self, review_dir: Path, candidates: list[Article],
                          backward_count: int = 0, forward_count: int = 0) -> None:
        (review_dir / CC_CANDIDATES_FILE).write_text(
            json.dumps({
                "backward_count": backward_count,
                "forward_count": forward_count,
                "candidates": [a.model_dump(mode="json") for a in candidates],
            }, indent=2)
        )

    def test_no_candidates_file_dies(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [])
        with pytest.raises(SystemExit):
            cc_save(review_dir, json.dumps({"decisions": []}))
        assert "error" in json.loads(capsys.readouterr().out)

    def test_include_decision_adds_to_screened(self, tmp_path, capsys):
        # Citation-chased includes are appended to articles_screened.json for Stage 9
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        existing = _make_article(doi="10.1/existing", screening_decision=ScreeningDecision.include)
        _write_screened(review_dir, [existing])
        new_candidate = _make_article(doi="10.1/new", title="New reference paper")
        self._write_candidates(review_dir, [new_candidate], backward_count=5)

        cc_save(review_dir, json.dumps({"decisions": [{"index": 0, "decision": "include", "rationale": "Relevant"}]}))
        capsys.readouterr()

        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert len(screened) == 2

    def test_exclude_decision_not_added(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [_make_article(doi="10.1/existing")])
        self._write_candidates(review_dir, [_make_article(doi="10.1/new")])

        cc_save(review_dir, json.dumps({"decisions": [{"index": 0, "decision": "exclude", "rationale": "Off-topic"}]}))
        capsys.readouterr()

        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert len(screened) == 1  # only original, exclude not appended

    def test_default_borderline_decision_added_to_screened(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [_make_article(doi="10.1/existing")])
        self._write_candidates(review_dir, [_make_article(doi="10.1/new")])

        cc_save(review_dir, json.dumps({"decisions": []}))
        capsys.readouterr()

        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert len(screened) == 2  # borderline now appended alongside includes (Issue 31)

    def test_status_updated_to_citation_chased(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [])
        self._write_candidates(review_dir, [])

        cc_save(review_dir, json.dumps({"decisions": []}))
        capsys.readouterr()

        assert state_manager.load(review_dir).status == ReviewStatus.citation_chased

    def test_prisma_citation_chasing_added_set(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [_make_article(doi="10.1/existing")])
        candidates = [_make_article(doi="10.1/a"), _make_article(doi="10.1/b")]
        self._write_candidates(review_dir, candidates)

        cc_save(review_dir, json.dumps({"decisions": [
            {"index": 0, "decision": "include", "rationale": "Relevant"},
            {"index": 1, "decision": "exclude", "rationale": "Off-topic"},
        ]}))
        capsys.readouterr()

        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.citation_chasing_added == 1

    def test_prisma_citation_chasing_screened_set(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [_make_article(doi="10.1/existing")])
        candidates = [_make_article(doi="10.1/a"), _make_article(doi="10.1/b")]
        self._write_candidates(review_dir, candidates, backward_count=3, forward_count=4)

        cc_save(review_dir, json.dumps({"decisions": [
            {"index": 0, "decision": "include", "rationale": "Relevant"},
            {"index": 1, "decision": "exclude", "rationale": "Off-topic"},
        ]}))
        capsys.readouterr()

        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.citation_chasing_screened == 2
        assert saved.prisma_counts.citation_chasing_identified == 7  # 3 + 4

    def test_search_log_appended(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [])
        (review_dir / "search_log.md").write_text("# Prior log\n")
        self._write_candidates(review_dir, [], backward_count=3, forward_count=2)

        cc_save(review_dir, json.dumps({"decisions": []}))
        capsys.readouterr()

        log = (review_dir / "search_log.md").read_text()
        assert "Prior log" in log
        assert "Citation Chasing" in log

    def test_candidates_file_deleted_after_save(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [])
        self._write_candidates(review_dir, [])

        cc_save(review_dir, json.dumps({"decisions": []}))
        capsys.readouterr()

        assert not (review_dir / CC_CANDIDATES_FILE).exists()

    def test_output_includes_new_includes_count(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [])
        self._write_candidates(review_dir, [_make_article(doi="10.1/x")])

        cc_save(review_dir, json.dumps({"decisions": [{"index": 0, "decision": "include", "rationale": "OK"}]}))
        out = json.loads(capsys.readouterr().out)
        assert out["new_includes"] == 1

    def test_included_article_has_citation_chased_flag(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [])
        self._write_candidates(review_dir, [_make_article(doi="10.1/x")])

        cc_save(review_dir, json.dumps({"decisions": [{"index": 0, "decision": "include", "rationale": "OK"}]}))
        capsys.readouterr()

        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert screened[0]["citation_chased"] is True


# ===========================================================================
# gap_analyzer — prepare
# ===========================================================================

class TestGapAnalyzerPrepare:
    def test_wrong_status_dies(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_included(review_dir, [_make_article()])
        with pytest.raises(SystemExit):
            ga_prepare(review_dir)
        assert "error" in json.loads(capsys.readouterr().out)

    def test_outputs_review_question(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.evidence_classified)
        _write_included(review_dir, [_make_article()])
        out = _capture_ga_prepare(capsys, review_dir)
        assert "Do care transitions reduce CHF readmission?" in out["review_question"]

    def test_outputs_total_included(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.evidence_classified)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(5)])
        out = _capture_ga_prepare(capsys, review_dir)
        assert out["total_included"] == 5

    def test_article_summary_present(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.evidence_classified)
        _write_included(review_dir, [_make_article(title="CHF readmission study")])
        out = _capture_ga_prepare(capsys, review_dir)
        assert "CHF readmission study" in out["article_summary"]

    def test_pico_text_present(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.evidence_classified)
        _write_included(review_dir, [_make_article()])
        out = _capture_ga_prepare(capsys, review_dir)
        assert "Population" in out["pico_text"]
        assert "Adult patients" in out["pico_text"]

    def test_gap_task_in_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.evidence_classified)
        _write_included(review_dir, [])
        out = _capture_ga_prepare(capsys, review_dir)
        assert "gap_task" in out
        assert "iterate|stop" in out["gap_task"]

    def test_recency_stats_in_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.evidence_classified)
        _write_included(review_dir, [_make_article(year=2022)])
        out = _capture_ga_prepare(capsys, review_dir)
        assert out["recency"]["newest"] == 2022
        assert out["recency"]["oldest"] == 2022


# ===========================================================================
# gap_analyzer — save
# ===========================================================================

class TestGapAnalyzerSave:
    def _setup(self, tmp_path, articles=None):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.evidence_classified)
        if articles is None:
            articles = [_make_article()]
        _write_included(review_dir, articles)
        return review_dir

    def test_writes_gap_analysis_md(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_ga_save(capsys, review_dir, _minimal_gap_data())
        assert (review_dir / "gap_analysis.md").exists()

    def test_writes_gap_analysis_json(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_ga_save(capsys, review_dir, _minimal_gap_data())
        assert (review_dir / "gap_analysis.json").exists()

    def test_gap_analysis_json_matches_input(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        gap_data = _minimal_gap_data(recommendation="iterate")
        _capture_ga_save(capsys, review_dir, gap_data)
        saved = json.loads((review_dir / "gap_analysis.json").read_text())
        assert saved["recommendation"] == "iterate"

    def test_status_updated_to_gap_analyzed(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_ga_save(capsys, review_dir, _minimal_gap_data())
        assert state_manager.load(review_dir).status == ReviewStatus.gap_analyzed

    def test_output_includes_recommendation(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        out = _capture_ga_save(capsys, review_dir, _minimal_gap_data(recommendation="stop"))
        assert out["recommendation"] == "stop"

    def test_output_includes_pico_gap_count(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        gap_data = _minimal_gap_data(pico_gaps=[
            {"aspect": "O", "gap": "Mortality not addressed", "severity": "medium"}
        ])
        out = _capture_ga_save(capsys, review_dir, gap_data)
        assert out["pico_gaps"] == 1

    def test_output_includes_suggested_query_count(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        out = _capture_ga_save(capsys, review_dir, _minimal_gap_data())
        assert out["suggested_queries"] == 1


# ===========================================================================
# iteration_manager — merge
# ===========================================================================

class TestIterationManagerMerge:
    def _write_candidates(self, review_dir: Path, articles: list[Article],
                          updated_seen: list[str] | None = None) -> None:
        (review_dir / IM_CANDIDATES_FILE).write_text(
            json.dumps({
                "updated_seen": updated_seen or [],
                "candidates": [a.model_dump(mode="json") for a in articles],
            }, indent=2)
        )

    def test_no_candidates_file_dies(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [])
        with pytest.raises(SystemExit):
            im_merge(review_dir, json.dumps({"decisions": []}))
        assert "error" in json.loads(capsys.readouterr().out)

    def test_include_candidate_flagged_but_not_yet_included(self, tmp_path, capsys):
        # A new iteration include is appended to articles_screened.json (flagged iteration_added)
        # but NOT to articles_included.json — it must clear the scoped full-text pass first.
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        existing = _make_article(doi="10.1/existing")
        _write_included(review_dir, [existing])
        _write_screened(review_dir, [existing])
        new_candidate = _make_article(doi="10.1/new")
        self._write_candidates(review_dir, [new_candidate])

        _capture_im_merge(capsys, review_dir, [
            {"article_index": 0, "decision": "include", "reason": "Relevant", "reason_category": None}
        ])

        included = json.loads((review_dir / "articles_included.json").read_text())
        assert len(included) == 1  # only the existing primary; new candidate not yet included
        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert next(a for a in screened if a["doi"] == "10.1/new")["iteration_added"] is True

    def test_exclude_decision_not_in_included(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [_make_article(doi="10.1/existing")])
        self._write_candidates(review_dir, [_make_article(doi="10.1/new")])

        _capture_im_merge(capsys, review_dir, [
            {"article_index": 0, "decision": "exclude", "reason": "Off-topic", "reason_category": "wrong_population"}
        ])

        included = json.loads((review_dir / "articles_included.json").read_text())
        assert len(included) == 1

    def test_screened_file_updated(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [])
        _write_screened(review_dir, [_make_article(doi="10.1/prior")])
        new_candidate = _make_article(doi="10.1/new")
        self._write_candidates(review_dir, [new_candidate])

        _capture_im_merge(capsys, review_dir, [
            {"article_index": 0, "decision": "exclude", "reason": "Off-topic", "reason_category": "other"}
        ])

        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert len(screened) == 2

    def test_status_updated_to_citation_chased(self, tmp_path, capsys):
        # merge() advances to citation_chased so the new candidates re-enter Stage 9 → 9.5 → 10
        # (scoped full-text assessment), held to the same gates as the primary set.
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [])
        self._write_candidates(review_dir, [])

        _capture_im_merge(capsys, review_dir, [])
        assert state_manager.load(review_dir).status == ReviewStatus.citation_chased

    def test_merge_flags_iteration_candidates_without_adding_to_included(self, tmp_path, capsys):
        # New candidates are flagged iteration_added in articles_screened.json but must NOT be
        # written to articles_included.json — they clear full-text assessment first.
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [_make_article(doi="10.1/prior")])
        self._write_candidates(review_dir, [_make_article(doi="10.1/new")])

        _capture_im_merge(capsys, review_dir, [
            {"article_index": 0, "decision": "include", "reason": "OK", "reason_category": None},
        ])
        included = json.loads((review_dir / "articles_included.json").read_text())
        assert len(included) == 1  # only the prior primary; iteration candidate NOT added yet
        screened = json.loads((review_dir / "articles_screened.json").read_text())
        new_row = next(a for a in screened if a["doi"] == "10.1/new")
        assert new_row["iteration_added"] is True

    def test_merge_tolerates_malformed_decision(self, tmp_path, capsys):
        # A decision entry missing "article_index" must not crash merge() (LLM output drift).
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [])
        self._write_candidates(review_dir, [_make_article(doi="10.1/new")])

        _capture_im_merge(capsys, review_dir, [{"decision": "include"}])  # no article_index
        # candidate defaults to borderline; no crash, review advances.
        assert state_manager.load(review_dir).status == ReviewStatus.citation_chased

    def test_iteration_count_incremented(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed, iterations=1)
        _write_included(review_dir, [])
        self._write_candidates(review_dir, [])

        _capture_im_merge(capsys, review_dir, [])
        assert state_manager.load(review_dir).iterations == 2

    def test_prisma_counts_updated(self, tmp_path, capsys):
        review, review_dir = _make_review(
            tmp_path,
            status=ReviewStatus.gap_analyzed,
            prisma_counts=PRISMACounts(identified=100, screened=80, eligibility=20, included=15),
        )
        _write_included(review_dir, [_make_article(doi="10.1/existing")])
        candidates = [_make_article(doi="10.1/a"), _make_article(doi="10.1/b")]
        self._write_candidates(review_dir, candidates)

        _capture_im_merge(capsys, review_dir, [
            {"article_index": 0, "decision": "include", "reason": "OK", "reason_category": None},
            {"article_index": 1, "decision": "exclude", "reason": "Off-topic", "reason_category": "other"},
        ])

        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.screened == 82  # 80 + 2 (both new candidates were screened)
        # included is NOT bumped by merge — the new include must clear the scoped full-text pass
        # (fulltext_assessor --iteration-only) before it counts as included.
        assert saved.prisma_counts.included == 15

    def test_seen_ids_updated_from_candidates_file(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [])
        new_candidate = _make_article(doi="10.1/new")
        self._write_candidates(
            review_dir, [new_candidate],
            updated_seen=["doi:10.1/new", "doi:10.1/existing"]
        )

        _capture_im_merge(capsys, review_dir, [])

        saved = state_manager.load(review_dir)
        assert "doi:10.1/new" in saved.seen_article_ids

    def test_candidates_file_deleted_after_merge(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [])
        self._write_candidates(review_dir, [])

        _capture_im_merge(capsys, review_dir, [])
        assert not (review_dir / IM_CANDIDATES_FILE).exists()

    def test_no_decisions_leaves_all_as_borderline(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [])
        self._write_candidates(review_dir, [_make_article(doi="10.1/new")])

        _capture_im_merge(capsys, review_dir, [])

        included = json.loads((review_dir / "articles_included.json").read_text())
        assert len(included) == 0  # borderline = not in included
