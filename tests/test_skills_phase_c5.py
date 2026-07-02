"""Unit tests for Phase C5 — subscription conversion of synthesizer and output_formatter.

Tests the --prepare / --save CLI patterns for both skills.
No LLM or Anthropic API calls. All LLM work runs in the Claude Code conversation.
Also includes the strict (non-xfail) no-anthropic-import guard.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

import sys
sys.path.insert(0, str(PROJECT_ROOT))

from src.agent import state as state_manager
from src.models.article import Article, EvidenceLevel, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import PRISMACounts, Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.synthesizer import prepare as synth_prepare, save as synth_save
from src.skills.output_formatter import prepare as fmt_prepare, save as fmt_save



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


def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-hf-2026-04"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="test-hf-2026-04",
        raw_question="Do care transitions reduce HF readmission?",
        review_type=ReviewType.systematic,
        pico=_make_pico(),
        screening_mode=ScreeningMode.thorough,
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
        authors=["Smith J", "Jones A"],
        year=2022,
        journal="NEJM",
        source_dbs=["pubmed"],
        study_design="RCT",
        evidence_level=EvidenceLevel.level_II,
        quality_score=0.82,
        primary_outcomes=["30-day all-cause readmission"],
        effect_sizes="OR 0.74 (95% CI 0.62–0.88)",
        screening_decision=ScreeningDecision.include,
    )
    defaults.update(kwargs)
    return Article(**defaults)


def _write_included(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_included.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _write_synthesis(review_dir: Path, synthesis: dict | None = None) -> None:
    if synthesis is None:
        synthesis = {
            "review_id": "test-hf-2026-04",
            "question": "Do care transitions reduce HF readmission?",
            "total_included": 2,
            "narrative": {
                "by_outcome": [
                    {"outcome": "30-day all-cause readmission", "narrative": "Evidence supports.", "highest_level": "II", "article_count": 2}
                ],
                "overall_narrative": "Care transitions reduce readmission.",
                "key_findings": ["26% RRR in RCTs"],
                "evidence_quality_summary": "Moderate quality.",
                "limitations": ["Short follow-up"],
            },
            "evidence_map": [],
            "quantitative_scaffold": [],
        }
    (review_dir / "synthesis.json").write_text(json.dumps(synthesis, indent=2))


def _capture_synth_prepare(capsys, review_dir: Path) -> dict:
    synth_prepare(review_dir)
    return json.loads(capsys.readouterr().out)


def _capture_synth_save(capsys, review_dir: Path, data: dict) -> dict:
    synth_save(review_dir, json.dumps(data))
    return json.loads(capsys.readouterr().out)


def _capture_fmt_prepare(capsys, review_dir: Path) -> dict:
    fmt_prepare(review_dir)
    return json.loads(capsys.readouterr().out)


def _capture_fmt_save(capsys, review_dir: Path, data: dict) -> dict:
    fmt_save(review_dir, json.dumps(data))
    return json.loads(capsys.readouterr().out)


def _minimal_synthesis_response(**kwargs) -> dict:
    base = {
        "by_outcome": [{"outcome": "30-day readmission", "narrative": "RCTs show benefit.", "highest_level": "II", "article_count": 2}],
        "overall_narrative": "Care transitions reduce readmission.",
        "key_findings": ["26% RRR in RCTs"],
        "evidence_quality_summary": "Moderate quality.",
        "limitations": ["Short follow-up"],
    }
    base.update(kwargs)
    return base


# ===========================================================================
# synthesizer — prepare
# ===========================================================================

class TestSynthesizerPrepare:
    def test_wrong_status_dies(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.citation_chased)
        _write_included(review_dir, [_make_article()])
        with pytest.raises(SystemExit):
            synth_prepare(review_dir)
        assert "error" in json.loads(capsys.readouterr().out)

    def test_outputs_review_question(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [_make_article()])
        out = _capture_synth_prepare(capsys, review_dir)
        assert "Do care transitions reduce HF readmission?" in out["review_question"]

    def test_outputs_pico_text(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [_make_article()])
        out = _capture_synth_prepare(capsys, review_dir)
        assert "P:" in out["pico_text"]
        assert "Adult inpatients" in out["pico_text"]

    def test_outputs_total_articles(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [_make_article(pmid=str(i)) for i in range(4)])
        out = _capture_synth_prepare(capsys, review_dir)
        assert out["total_articles"] == 4

    def test_synthesis_context_present(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [_make_article(title="Landmark CHF RCT")])
        out = _capture_synth_prepare(capsys, review_dir)
        assert "Landmark CHF RCT" in out["synthesis_context"]

    def test_pico_outcomes_in_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [_make_article()])
        out = _capture_synth_prepare(capsys, review_dir)
        assert "30-day all-cause readmission" in out["pico_outcomes"]
        assert "all-cause mortality" in out["pico_outcomes"]

    def test_synthesis_task_in_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [_make_article()])
        out = _capture_synth_prepare(capsys, review_dir)
        assert "synthesis_task" in out
        assert "by_outcome" in out["synthesis_task"]

    def test_empty_articles_ok(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_included(review_dir, [])
        out = _capture_synth_prepare(capsys, review_dir)
        assert out["total_articles"] == 0
        assert out["synthesis_context"] == ""


# ===========================================================================
# synthesizer — save
# ===========================================================================

class TestSynthesizerSave:
    def _setup(self, tmp_path, articles=None):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        if articles is None:
            articles = [_make_article()]
        _write_included(review_dir, articles)
        return review_dir

    def test_writes_synthesis_json(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_synth_save(capsys, review_dir, _minimal_synthesis_response())
        assert (review_dir / "synthesis.json").exists()

    def test_synthesis_json_has_narrative(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_synth_save(capsys, review_dir, _minimal_synthesis_response())
        data = json.loads((review_dir / "synthesis.json").read_text())
        assert "narrative" in data
        assert "by_outcome" in data["narrative"]

    def test_synthesis_json_has_evidence_map(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_synth_save(capsys, review_dir, _minimal_synthesis_response())
        data = json.loads((review_dir / "synthesis.json").read_text())
        assert "evidence_map" in data
        assert isinstance(data["evidence_map"], list)

    def test_synthesis_json_has_quantitative_scaffold(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(effect_sizes="OR 0.74")])
        _capture_synth_save(capsys, review_dir, _minimal_synthesis_response())
        data = json.loads((review_dir / "synthesis.json").read_text())
        assert len(data["quantitative_scaffold"]) == 1

    def test_evidence_map_total_equals_included_count(self, tmp_path, capsys):
        articles = [_make_article(pmid=str(i), primary_outcomes=[]) for i in range(3)]
        review_dir = self._setup(tmp_path, articles)
        _capture_synth_save(capsys, review_dir, _minimal_synthesis_response())
        data = json.loads((review_dir / "synthesis.json").read_text())
        for row in data["evidence_map"]:
            assert row["total"] == 3

    def test_status_updated_to_synthesized(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_synth_save(capsys, review_dir, _minimal_synthesis_response())
        assert state_manager.load(review_dir).status == ReviewStatus.synthesized

    def test_output_includes_article_count(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid=str(i)) for i in range(3)])
        out = _capture_synth_save(capsys, review_dir, _minimal_synthesis_response())
        assert out["articles_synthesized"] == 3

    def test_output_includes_outcomes_addressed(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        out = _capture_synth_save(capsys, review_dir, _minimal_synthesis_response())
        assert out["outcomes_addressed"] == 1

    def test_output_includes_key_findings_count(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        out = _capture_synth_save(capsys, review_dir, _minimal_synthesis_response(
            key_findings=["Finding 1", "Finding 2"]
        ))
        assert out["key_findings"] == 2

    def test_synthesis_total_included_correct(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path, [_make_article(pmid=str(i)) for i in range(5)])
        _capture_synth_save(capsys, review_dir, _minimal_synthesis_response())
        data = json.loads((review_dir / "synthesis.json").read_text())
        assert data["total_included"] == 5


# ===========================================================================
# output_formatter — prepare
# ===========================================================================

class TestOutputFormatterPrepare:
    def test_wrong_status_dies(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.gap_analyzed)
        _write_synthesis(review_dir)
        with pytest.raises(SystemExit):
            fmt_prepare(review_dir)
        assert "error" in json.loads(capsys.readouterr().out)

    def test_missing_synthesis_json_dies(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        with pytest.raises(SystemExit):
            fmt_prepare(review_dir)
        assert "error" in json.loads(capsys.readouterr().out)

    def test_outputs_review_question(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_synthesis(review_dir)
        out = _capture_fmt_prepare(capsys, review_dir)
        assert "Do care transitions reduce HF readmission?" in out["review_question"]

    def test_outputs_pico_text(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_synthesis(review_dir)
        out = _capture_fmt_prepare(capsys, review_dir)
        assert "Adult inpatients" in out["pico_text"]

    def test_outputs_review_type(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_synthesis(review_dir)
        out = _capture_fmt_prepare(capsys, review_dir)
        assert out["review_type"] == "systematic"

    def test_outputs_search_summary(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_synthesis(review_dir)
        out = _capture_fmt_prepare(capsys, review_dir)
        ss = out["search_summary"]
        assert ss["identified"] == 200
        assert ss["included"] == 15
        assert ss["citation_chasing_added"] == 2

    def test_key_findings_from_synthesis(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_synthesis(review_dir)
        out = _capture_fmt_prepare(capsys, review_dir)
        assert "26% RRR in RCTs" in out["key_findings"]

    def test_executive_summary_task_in_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_synthesis(review_dir)
        out = _capture_fmt_prepare(capsys, review_dir)
        assert "executive_summary_task" in out
        assert "academic physician" in out["executive_summary_task"]

    def test_by_outcome_in_output(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_synthesis(review_dir)
        out = _capture_fmt_prepare(capsys, review_dir)
        assert len(out["by_outcome"]) == 1
        assert out["by_outcome"][0]["outcome"] == "30-day all-cause readmission"


# ===========================================================================
# output_formatter — save
# ===========================================================================

class TestOutputFormatterSave:
    def _setup(self, tmp_path, articles=None):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        if articles is None:
            articles = [_make_article()]
        _write_included(review_dir, articles)
        _write_synthesis(review_dir)
        return review_dir

    def _exec_summary(self) -> str:
        return "# Executive Summary\n\nCare transitions reduce 30-day readmission in heart failure patients."

    def test_writes_executive_summary(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_fmt_save(capsys, review_dir, {"executive_summary": self._exec_summary()})
        assert (review_dir / "executive_summary.md").exists()
        assert "Care transitions" in (review_dir / "executive_summary.md").read_text()

    def test_writes_prisma_flow(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_fmt_save(capsys, review_dir, {"executive_summary": self._exec_summary()})
        assert (review_dir / "prisma_flow.md").exists()

    def test_writes_ris_citations(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_fmt_save(capsys, review_dir, {"executive_summary": self._exec_summary()})
        assert (review_dir / "citations.ris").exists()

    def test_writes_bibtex_citations(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_fmt_save(capsys, review_dir, {"executive_summary": self._exec_summary()})
        assert (review_dir / "citations.bib").exists()

    def test_writes_evidence_table_if_missing(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_fmt_save(capsys, review_dir, {"executive_summary": self._exec_summary()})
        assert (review_dir / "evidence_table.csv").exists()

    def test_does_not_overwrite_evidence_table(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        (review_dir / "evidence_table.csv").write_text("existing,content\n")
        _capture_fmt_save(capsys, review_dir, {"executive_summary": self._exec_summary()})
        assert "existing,content" in (review_dir / "evidence_table.csv").read_text()

    def test_stub_files_created(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_fmt_save(capsys, review_dir, {"executive_summary": self._exec_summary()})
        assert (review_dir / "search_log.md").exists()
        assert (review_dir / "retrieval_list.csv").exists()

    def test_status_updated_to_complete(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        _capture_fmt_save(capsys, review_dir, {"executive_summary": self._exec_summary()})
        assert state_manager.load(review_dir).status == ReviewStatus.complete

    def test_output_includes_artifact_count(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        (review_dir / "gap_analysis.md").write_text("# Gap Analysis")
        (review_dir / "protocol.md").write_text("# Protocol")
        out = _capture_fmt_save(capsys, review_dir, {"executive_summary": self._exec_summary()})
        assert out["artifacts_written"] >= 7

    def test_output_includes_word_count(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        summary = "Word " * 100
        out = _capture_fmt_save(capsys, review_dir, {"executive_summary": summary})
        assert out["executive_summary_words"] == 100

    def test_artifacts_missing_list_accurate(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        out = _capture_fmt_save(capsys, review_dir, {"executive_summary": self._exec_summary()})
        assert "gap_analysis.md" in out["artifacts_missing"]
        assert "protocol.md" in out["artifacts_missing"]
        assert "executive_summary.md" not in out["artifacts_missing"]

    def test_empty_executive_summary_ok(self, tmp_path, capsys):
        review_dir = self._setup(tmp_path)
        out = _capture_fmt_save(capsys, review_dir, {"executive_summary": ""})
        assert out["executive_summary_words"] == 0
        assert (review_dir / "executive_summary.md").exists()


# ===========================================================================
# output_formatter — PRISMA recompute (#19)
# ===========================================================================

class TestOutputFormatterPrismaRecompute:
    def _setup_with_screened(self, tmp_path, screened_articles=None, prisma_excluded_screening=None):
        prisma_override = {}
        if prisma_excluded_screening is not None:
            prisma_override["excluded_screening"] = prisma_excluded_screening
        _, review_dir = _make_review(
            tmp_path,
            status=ReviewStatus.synthesized,
            prisma_counts=PRISMACounts(
                identified=200, screened=180, eligibility=30, included=15,
                citation_chasing_added=2,
                excluded_screening=prisma_override.get("excluded_screening", {"not relevant": 120}),
                excluded_fulltext={},
            ),
        )
        if screened_articles is not None:
            (review_dir / "articles_screened.json").write_text(
                json.dumps(screened_articles, indent=2)
            )
        _write_included(review_dir, [_make_article()])
        _write_synthesis(review_dir)
        return review_dir

    def test_recomputes_excluded_screening_from_article_file(self, tmp_path, capsys):
        screened = [
            {"title": "A", "screening_decision": "exclude", "screening_reason_category": "wrong_population", "source_dbs": ["pubmed"]},
            {"title": "B", "screening_decision": "exclude", "screening_reason_category": "wrong_outcome", "source_dbs": ["pubmed"]},
            {"title": "C", "screening_decision": "include", "screening_reason_category": None, "source_dbs": ["pubmed"]},
        ]
        review_dir = self._setup_with_screened(
            tmp_path,
            screened_articles=screened,
            prisma_excluded_screening={"not relevant": 100},  # stale
        )
        _capture_fmt_prepare(capsys, review_dir)
        review = state_manager.load(review_dir)
        assert review.prisma_counts.excluded_screening.get("wrong_population") == 1
        assert review.prisma_counts.excluded_screening.get("wrong_outcome") == 1
        assert sum(review.prisma_counts.excluded_screening.values()) == 2

    def test_recompute_does_not_touch_excluded_fulltext_if_populated(self, tmp_path, capsys):
        _, review_dir = _make_review(
            tmp_path,
            status=ReviewStatus.synthesized,
            prisma_counts=PRISMACounts(
                identified=50, screened=40, eligibility=10, included=7,
                excluded_fulltext={"no full text": 2, "wrong design": 1},
            ),
        )
        _write_included(review_dir, [_make_article()])
        _write_synthesis(review_dir)
        _capture_fmt_prepare(capsys, review_dir)
        review = state_manager.load(review_dir)
        assert review.prisma_counts.excluded_fulltext == {"no full text": 2, "wrong design": 1}

    def test_fallback_excluded_fulltext_when_empty_but_eligibility_gt_included(self, tmp_path, capsys):
        _, review_dir = _make_review(
            tmp_path,
            status=ReviewStatus.synthesized,
            prisma_counts=PRISMACounts(
                identified=50, screened=40, eligibility=10, included=5,
                excluded_fulltext={},
            ),
        )
        _write_included(review_dir, [_make_article()])
        _write_synthesis(review_dir)
        _capture_fmt_prepare(capsys, review_dir)
        review = state_manager.load(review_dir)
        assert sum(review.prisma_counts.excluded_fulltext.values()) == 5

    def test_no_screened_file_does_not_crash(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.synthesized)
        _write_included(review_dir, [_make_article()])
        _write_synthesis(review_dir)
        out = _capture_fmt_prepare(capsys, review_dir)
        assert "review_question" in out

    def test_save_recomputes_excluded_screening(self, tmp_path, capsys):
        # The terminal --save must recompute PRISMA counts from the article files, not only
        # prepare(), so a direct --save (or one after a stale-state resume) publishes correct
        # numbers rather than persisting whatever stale counts state.json happens to hold.
        screened = [
            {"title": "A", "screening_decision": "exclude", "screening_reason_category": "wrong_population", "source_dbs": ["pubmed"]},
            {"title": "B", "screening_decision": "exclude", "screening_reason_category": "wrong_population", "source_dbs": ["pubmed"]},
            {"title": "C", "screening_decision": "include", "screening_reason_category": None, "source_dbs": ["pubmed"]},
        ]
        review_dir = self._setup_with_screened(
            tmp_path,
            screened_articles=screened,
            prisma_excluded_screening={"not relevant": 100},  # stale
        )
        _capture_fmt_save(capsys, review_dir, {"executive_summary": "# S\n\nBody."})
        review = state_manager.load(review_dir)
        assert review.prisma_counts.excluded_screening.get("wrong_population") == 2
        assert "not relevant" not in review.prisma_counts.excluded_screening


# ===========================================================================
# No anthropic import guard — strict (all conversions complete)
# ===========================================================================

class TestNoAnthropicImportStrict:
    """All skill conversions are done. src/ must contain zero anthropic imports."""

    def _src_python_files(self) -> list[Path]:
        return list((PROJECT_ROOT / "src").rglob("*.py"))

    def test_no_import_anthropic(self):
        violations = []
        for path in self._src_python_files():
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("import anthropic") or stripped.startswith("from anthropic"):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {stripped}")
        assert violations == [], (
            "anthropic SDK imports found in src/:\n" + "\n".join(violations)
        )
