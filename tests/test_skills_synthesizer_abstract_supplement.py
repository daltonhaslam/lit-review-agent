"""Unit tests for Phase 6 — abstract supplement (opt-in, protocol-safe).

Tests:
  TestPrepareDefault (3)        — without --include-abstract-supplement, no supplement emitted
  TestPrepareOptIn (5)          — with flag, supplement filters to abstract-only includes/borderlines
  TestSynthesisTask (3)         — task text picks up supplement clause only when supplement non-empty
  TestSaveAbstractSupplement (4)— save() persists abstract_supplement at top level, never in narrative/evidence_map
  TestExecutiveSummaryAppendix (4) — output_formatter.save appends labeled appendix when present

No LLM or API calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import state as state_manager
from src.models.article import Article, EvidenceLevel, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.synthesizer import (
    _SYNTHESIS_TASK_BASE,
    _SYNTHESIS_TASK_SUPPLEMENT,
    _count_abstract_supplement,
    _load_abstract_supplement,
    _synthesis_task,
    prepare as syn_prepare,
    save as syn_save,
)
from src.skills.output_formatter import (
    _format_abstract_supplement_appendix,
    save as fmt_save,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Adult inpatients with heart failure",
        I="Care transitions",
        C="Usual care",
        O=["30-day readmission"],
        inclusion_criteria=["Adults ≥18"],
        exclusion_criteria=["Pediatric"],
    )
    defaults.update(kwargs)
    return PICO(**defaults)


def _make_review(tmp_path: Path, status=ReviewStatus.gap_analyzed, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "supplement-review"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="supplement-review",
        raw_question="Do care transitions reduce HF readmission?",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        status=status,
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
        title="A study",
        authors=["Smith J"],
        year=2022,
        journal="NEJM",
        source_dbs=["pubmed"],
        screening_decision=ScreeningDecision.include,
        evidence_level=EvidenceLevel.level_II,
        study_design="RCT",
        quality_score=0.85,
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


def _capture_prepare(capsys, review_dir: Path, *, include_abstract_supplement: bool = False) -> dict:
    syn_prepare(review_dir, include_abstract_supplement=include_abstract_supplement)
    return json.loads(capsys.readouterr().out)


# ===========================================================================
# TestPrepareDefault
# ===========================================================================

class TestPrepareDefault:
    def test_no_supplement_field_when_flag_off(self, tmp_path, capsys):
        included = [_make_article(pmid="1", doi="10.1/a")]
        screened = included + [_make_article(pmid="2", doi="10.1/b", screening_decision=ScreeningDecision.include)]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, included)
        _write_screened(review_dir, screened)
        out = _capture_prepare(capsys, review_dir, include_abstract_supplement=False)
        assert "abstract_supplement" not in out

    def test_synthesis_task_is_base_when_flag_off(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid="1")])
        out = _capture_prepare(capsys, review_dir, include_abstract_supplement=False)
        assert out["synthesis_task"] == _SYNTHESIS_TASK_BASE
        assert "ABSTRACT SUPPLEMENT" not in out["synthesis_task"]

    def test_dies_when_articles_screened_missing_and_flag_on(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, [_make_article(pmid="1")])
        with pytest.raises(SystemExit):
            _capture_prepare(capsys, review_dir, include_abstract_supplement=True)


# ===========================================================================
# TestPrepareOptIn
# ===========================================================================

class TestPrepareOptIn:
    def test_supplement_emits_abstract_only_includes(self, tmp_path, capsys):
        included = [_make_article(pmid="1", doi="10.1/a", title="In set")]
        screened = [
            included[0],  # in articles_included → excluded from supplement
            _make_article(pmid="2", doi="10.1/b", title="Abstract-only include",
                          screening_decision=ScreeningDecision.include),
            _make_article(pmid="3", doi="10.1/c", title="Excluded at screening",
                          screening_decision=ScreeningDecision.exclude),
        ]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, included)
        _write_screened(review_dir, screened)
        out = _capture_prepare(capsys, review_dir, include_abstract_supplement=True)
        assert len(out["abstract_supplement"]) == 1
        assert out["abstract_supplement"][0]["title"] == "Abstract-only include"

    def test_supplement_includes_borderlines(self, tmp_path, capsys):
        included = [_make_article(pmid="1", doi="10.1/a")]
        screened = [
            included[0],
            _make_article(pmid="2", doi="10.1/b", screening_decision=ScreeningDecision.borderline),
        ]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, included)
        _write_screened(review_dir, screened)
        out = _capture_prepare(capsys, review_dir, include_abstract_supplement=True)
        decisions = [a["screening_decision"] for a in out["abstract_supplement"]]
        assert "borderline" in decisions

    def test_supplement_skips_excludes_and_pending(self, tmp_path, capsys):
        included = []
        screened = [
            _make_article(pmid="2", screening_decision=ScreeningDecision.exclude),
            _make_article(pmid="3", screening_decision=ScreeningDecision.pending),
        ]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, included)
        _write_screened(review_dir, screened)
        out = _capture_prepare(capsys, review_dir, include_abstract_supplement=True)
        assert "abstract_supplement" not in out

    def test_supplement_marks_evidence_basis_abstract_only(self, tmp_path, capsys):
        included = []
        screened = [_make_article(pmid="2", doi="10.1/b", screening_decision=ScreeningDecision.include)]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, included)
        _write_screened(review_dir, screened)
        out = _capture_prepare(capsys, review_dir, include_abstract_supplement=True)
        assert out["abstract_supplement"][0]["evidence_basis"] == "abstract_only"

    def test_synthesis_task_includes_supplement_clause(self, tmp_path, capsys):
        included = []
        screened = [_make_article(pmid="2", screening_decision=ScreeningDecision.include)]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, included)
        _write_screened(review_dir, screened)
        out = _capture_prepare(capsys, review_dir, include_abstract_supplement=True)
        assert "ABSTRACT SUPPLEMENT" in out["synthesis_task"]
        assert "abstract_supplement_summary" in out["synthesis_task"]


# ===========================================================================
# TestSynthesisTask
# ===========================================================================

class TestSynthesisTask:
    def test_base_only_when_no_supplement(self):
        assert _synthesis_task(False) == _SYNTHESIS_TASK_BASE

    def test_appends_supplement_when_present(self):
        assert _synthesis_task(True) == _SYNTHESIS_TASK_BASE + _SYNTHESIS_TASK_SUPPLEMENT

    def test_count_abstract_supplement_matches_load(self, tmp_path):
        included = [_make_article(pmid="1", doi="10.1/a")]
        screened = included + [
            _make_article(pmid="2", doi="https://doi.org/10.1/B",
                          screening_decision=ScreeningDecision.include),
            _make_article(pmid="3", doi="10.1/c",
                          screening_decision=ScreeningDecision.exclude),
        ]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, included)
        _write_screened(review_dir, screened)
        assert _count_abstract_supplement(review_dir) == 1


# ===========================================================================
# TestSaveAbstractSupplement
# ===========================================================================

def _minimal_synthesis_payload(extra: dict | None = None) -> str:
    payload = {
        "by_outcome": [{"outcome": "30-day readmission", "narrative": "Smith 2022 reduced X.",
                        "highest_level": "II", "article_count": 1}],
        "overall_narrative": "Smith 2022 found...",
        "key_findings": ["finding"],
        "evidence_quality_summary": "moderate",
        "limitations": ["small N"],
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload)


class TestSaveAbstractSupplement:
    def test_save_writes_top_level_supplement_when_summary_present(self, tmp_path):
        included = [_make_article(pmid="1")]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, included)
        payload = _minimal_synthesis_payload({"abstract_supplement_summary": "Two abstract-only studies (Doe 2024, Roe 2023)..."})
        syn_save(review_dir, payload)
        synthesis = json.loads((review_dir / "synthesis.json").read_text())
        assert synthesis["abstract_supplement"].startswith("Two abstract-only")

    def test_save_omits_supplement_key_when_summary_absent(self, tmp_path):
        included = [_make_article(pmid="1")]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, included)
        syn_save(review_dir, _minimal_synthesis_payload())
        synthesis = json.loads((review_dir / "synthesis.json").read_text())
        assert "abstract_supplement" not in synthesis

    def test_save_does_not_fold_summary_into_narrative(self, tmp_path):
        included = [_make_article(pmid="1")]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, included)
        payload = _minimal_synthesis_payload({"abstract_supplement_summary": "Supplemental..."})
        syn_save(review_dir, payload)
        synthesis = json.loads((review_dir / "synthesis.json").read_text())
        assert "abstract_supplement_summary" not in synthesis["narrative"]

    def test_save_keeps_evidence_map_and_scaffold_unchanged_with_supplement(self, tmp_path):
        included = [_make_article(pmid="1", evidence_level=EvidenceLevel.level_II,
                                   effect_sizes="OR 0.8", primary_outcomes=["30-day readmission"])]
        _, review_dir = _make_review(tmp_path)
        _write_included(review_dir, included)
        payload = _minimal_synthesis_payload({"abstract_supplement_summary": "Supplemental..."})
        syn_save(review_dir, payload)
        synthesis = json.loads((review_dir / "synthesis.json").read_text())
        assert synthesis["evidence_map"][0]["total"] == 1
        assert synthesis["evidence_map"][0]["by_design"]["RCT"] == 1
        assert len(synthesis["quantitative_scaffold"]) == 1


# ===========================================================================
# TestExecutiveSummaryAppendix
# ===========================================================================

class TestExecutiveSummaryAppendix:
    def test_appendix_helper_includes_label_and_caveat(self):
        rendered = _format_abstract_supplement_appendix("Smith 2022 reported X (abstract only).")
        assert "Appendix" in rendered
        assert "Not Full-Text Appraised" in rendered
        assert "do not treat them as part" in rendered.lower()
        assert "Smith 2022 reported X" in rendered

    def test_save_appends_appendix_when_synthesis_has_supplement(self, tmp_path):
        review, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_included(review_dir, [_make_article(pmid="1")])
        (review_dir / "synthesis.json").write_text(json.dumps({
            "review_id": review.review_id,
            "question": review.raw_question,
            "total_included": 1,
            "narrative": {"by_outcome": [], "overall_narrative": "", "key_findings": [],
                          "evidence_quality_summary": "", "limitations": []},
            "evidence_map": [],
            "quantitative_scaffold": [],
            "abstract_supplement": "Doe 2024 reported X (abstract only).",
        }))
        fmt_save(review_dir, json.dumps({"executive_summary": "# Summary\n\nMain narrative."}))
        content = (review_dir / "executive_summary.md").read_text()
        assert "# Summary" in content
        assert "Main narrative." in content
        assert "Appendix" in content
        assert "Doe 2024" in content

    def test_save_omits_appendix_when_no_supplement(self, tmp_path):
        review, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_included(review_dir, [_make_article(pmid="1")])
        (review_dir / "synthesis.json").write_text(json.dumps({
            "review_id": review.review_id,
            "question": review.raw_question,
            "total_included": 1,
            "narrative": {"by_outcome": [], "overall_narrative": "", "key_findings": [],
                          "evidence_quality_summary": "", "limitations": []},
            "evidence_map": [],
            "quantitative_scaffold": [],
        }))
        fmt_save(review_dir, json.dumps({"executive_summary": "# Summary\n\nMain narrative."}))
        content = (review_dir / "executive_summary.md").read_text()
        assert "Appendix" not in content
        assert content.strip() == "# Summary\n\nMain narrative."

    def test_load_abstract_supplement_filters_correctly(self, tmp_path):
        # DOI prefix differs across files (https://doi.org/ vs bare); _fingerprint must normalize.
        included_articles = [_make_article(pmid="1", doi="https://doi.org/10.1/A")]
        screened_articles = included_articles + [
            _make_article(pmid="2", doi="10.1/b",
                          screening_decision=ScreeningDecision.include),
            _make_article(pmid="3", doi="10.1/c",
                          screening_decision=ScreeningDecision.exclude),
        ]
        _, review_dir = _make_review(tmp_path)
        _write_screened(review_dir, screened_articles)
        result = _load_abstract_supplement(review_dir, "articles_screened.json", included_articles)
        pmids = sorted(r["pmid"] for r in result)
        assert pmids == ["2"]
