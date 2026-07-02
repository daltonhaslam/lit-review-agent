"""Unit tests for Phase C2 — abstract_screener subscription conversion.

Tests the --prepare / --save CLI pattern, batch offset logic, and
PRISMA count accumulation across batches. No LLM or API calls.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.abstract_screener import _build_pico_context, _decision_from_str, prepare, save


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Adult patients hospitalized with heart failure",
        I="Care transitions interventions",
        C="Usual care",
        O=["30-day all-cause readmission rate", "mortality"],
        inclusion_criteria=["Adults ≥18", "Primary HF diagnosis"],
        exclusion_criteria=["Pediatric population", "Non-English"],
    )
    defaults.update(kwargs)
    return PICO(**defaults)


def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "chf-2026-04"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="chf-2026-04",
        raw_question="Do care transitions reduce CHF readmission?",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        status=ReviewStatus.deduplicated,
        pico=_make_pico(),
        queries=QuerySet(
            pubmed=[Query(step_number="#1", query_string='"Heart Failure"[Mesh]', database="pubmed")],
        ),
    )
    defaults.update(kwargs)
    review = Review(**defaults)
    state_manager.save(review, review_dir)
    return review, review_dir


def _make_article(title: str = "A CHF study", **kwargs) -> Article:
    defaults = dict(title=title, authors=["Smith J"], year=2022, journal="NEJM", source_dbs=["pubmed"])
    defaults.update(kwargs)
    return Article(**defaults)


def _write_deduped(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_deduped.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=0) -> dict:
    prepare(review_dir, batch_size, batch_offset)
    return json.loads(capsys.readouterr().out)


def _decisions_json(decisions: list[dict]) -> str:
    return json.dumps({"decisions": decisions})


def _capture_save(capsys, review_dir, offset, decisions) -> dict:
    save(review_dir, offset, _decisions_json(decisions))
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# _build_pico_context — preserved helper
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
        review.pico = _make_pico(S="RCTs only")
        ctx = _build_pico_context(review)
        assert "S (Study design):" in ctx

    def test_omits_study_design_when_none(self, tmp_path):
        review, _ = _make_review(tmp_path)
        review.pico = _make_pico(S=None)
        ctx = _build_pico_context(review)
        assert "S (Study design):" not in ctx


# ---------------------------------------------------------------------------
# _decision_from_str — preserved helper
# ---------------------------------------------------------------------------

class TestDecisionFromStr:
    def test_include(self):
        assert _decision_from_str("include") == ScreeningDecision.include

    def test_exclude(self):
        assert _decision_from_str("exclude") == ScreeningDecision.exclude

    def test_borderline(self):
        assert _decision_from_str("borderline") == ScreeningDecision.borderline

    def test_invalid_defaults_to_borderline(self):
        assert _decision_from_str("unclear") == ScreeningDecision.borderline

    def test_case_insensitive(self):
        assert _decision_from_str("INCLUDE") == ScreeningDecision.include


# ---------------------------------------------------------------------------
# prepare()
# ---------------------------------------------------------------------------

class TestPrepare:
    def test_output_is_valid_json(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()])
        out = _capture_prepare(capsys, review_dir)
        assert isinstance(out, dict)

    def test_batch_number_starts_at_one(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()] * 25)
        out = _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert out["batch_number"] == 1

    def test_batch_number_increments_correctly(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()] * 45)
        out = _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=20)
        assert out["batch_number"] == 2

    def test_total_batches_ceiling(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()] * 45)
        out = _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert out["total_batches"] == math.ceil(45 / 20)

    def test_total_articles_correct(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()] * 37)
        out = _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert out["total_articles"] == 37

    def test_batch_contains_correct_article_count(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()] * 25)
        out = _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=0)
        assert len(out["articles"]) == 20

    def test_last_batch_is_partial(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()] * 25)
        out = _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=20)
        assert len(out["articles"]) == 5

    def test_article_index_equals_global_position(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid=str(i)) for i in range(25)])
        out = _capture_prepare(capsys, review_dir, batch_size=20, batch_offset=20)
        assert out["articles"][0]["article_index"] == 20
        assert out["articles"][4]["article_index"] == 24

    def test_article_fields_present(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(title="CHF study", pmid="123", year=2022)])
        out = _capture_prepare(capsys, review_dir)
        a = out["articles"][0]
        assert a["title"] == "CHF study"
        assert a["pmid"] == "123"
        assert a["year"] == 2022
        assert "abstract" in a
        assert "doi" in a

    def test_abstract_truncated_to_max_chars(self, tmp_path, capsys):
        long_abstract = "x" * 2000
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(abstract=long_abstract)])
        out = _capture_prepare(capsys, review_dir)
        assert len(out["articles"][0]["abstract"]) <= 1500

    def test_missing_abstract_placeholder(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(abstract=None)])
        out = _capture_prepare(capsys, review_dir)
        assert out["articles"][0]["abstract"] == "No abstract available"

    def test_pico_fields_present(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()])
        out = _capture_prepare(capsys, review_dir)
        assert "pico" in out
        assert "P" in out["pico"]
        assert "I" in out["pico"]
        assert "C" in out["pico"]
        assert "O" in out["pico"]

    def test_criteria_present(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()])
        out = _capture_prepare(capsys, review_dir)
        assert isinstance(out["inclusion_criteria"], list)
        assert len(out["inclusion_criteria"]) > 0
        assert isinstance(out["exclusion_criteria"], list)

    def test_screening_task_present(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()])
        out = _capture_prepare(capsys, review_dir)
        assert "screening_task" in out
        assert "include" in out["screening_task"]
        assert "exclude" in out["screening_task"]

    def test_dies_on_wrong_status(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.retrieved)
        _write_deduped(review_dir, [_make_article()])
        with pytest.raises(SystemExit):
            prepare(review_dir, 20, 0)

    def test_dies_when_no_pico(self, tmp_path, capsys):
        review_dir = tmp_path / "no-pico"
        review_dir.mkdir()
        review = Review(review_id="no-pico", raw_question="test", status=ReviewStatus.deduplicated)
        state_manager.save(review, review_dir)
        _write_deduped(review_dir, [_make_article()])
        with pytest.raises(SystemExit):
            prepare(review_dir, 20, 0)

    def test_dies_when_offset_out_of_range(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article()] * 5)
        with pytest.raises(SystemExit):
            prepare(review_dir, 20, 10)


# ---------------------------------------------------------------------------
# save()
# ---------------------------------------------------------------------------

class TestSave:
    def test_writes_screened_file(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid="1")])
        decisions = [{"article_index": 0, "decision": "include", "reason": "Relevant", "reason_category": None}]
        _capture_save(capsys, review_dir, 0, decisions)
        assert (review_dir / "articles_screened.json").exists()

    def test_applies_include_decision(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid="1")])
        decisions = [{"article_index": 0, "decision": "include", "reason": "Relevant", "reason_category": None}]
        _capture_save(capsys, review_dir, 0, decisions)
        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert screened[0]["screening_decision"] == "include"

    def test_applies_exclude_decision_with_reason_category(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid="1")])
        decisions = [{"article_index": 0, "decision": "exclude", "reason": "Pediatric", "reason_category": "wrong_population"}]
        _capture_save(capsys, review_dir, 0, decisions)
        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert screened[0]["screening_decision"] == "exclude"
        assert screened[0]["screening_reason_category"] == "wrong_population"

    def test_stores_rationale(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid="1")])
        decisions = [{"article_index": 0, "decision": "include", "reason": "Matches PICO exactly", "reason_category": None}]
        _capture_save(capsys, review_dir, 0, decisions)
        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert screened[0]["screening_rationale"] == "Matches PICO exactly"

    def test_invalid_article_index_skipped(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid="1")])
        decisions = [
            {"article_index": 99, "decision": "include", "reason": "Out of range", "reason_category": None},
            {"article_index": 0, "decision": "include", "reason": "Valid", "reason_category": None},
        ]
        out = _capture_save(capsys, review_dir, 0, decisions)
        assert out["batch_saved"] == 2
        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert screened[0]["screening_decision"] == "include"

    def test_incomplete_when_articles_remain_pending(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid="1"), _make_article(pmid="2")])
        decisions = [{"article_index": 0, "decision": "include", "reason": "OK", "reason_category": None}]
        out = _capture_save(capsys, review_dir, 0, decisions)
        assert out["complete"] is False
        assert out["pending_remaining"] == 1

    def test_complete_when_all_decided(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid="1"), _make_article(pmid="2")])
        decisions = [
            {"article_index": 0, "decision": "include", "reason": "OK", "reason_category": None},
            {"article_index": 1, "decision": "exclude", "reason": "Wrong pop", "reason_category": "wrong_population"},
        ]
        out = _capture_save(capsys, review_dir, 0, decisions)
        assert out["complete"] is True
        assert out["pending_remaining"] == 0

    def test_finalizes_prisma_on_completion(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid="1"), _make_article(pmid="2")])
        decisions = [
            {"article_index": 0, "decision": "include", "reason": "OK", "reason_category": None},
            {"article_index": 1, "decision": "exclude", "reason": "Wrong pop", "reason_category": "wrong_population"},
        ]
        _capture_save(capsys, review_dir, 0, decisions)
        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.screened == 2
        assert saved.prisma_counts.excluded_screening["wrong_population"] == 1

    def test_updates_status_to_abstract_screening_on_completion(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid="1")])
        decisions = [{"article_index": 0, "decision": "include", "reason": "OK", "reason_category": None}]
        _capture_save(capsys, review_dir, 0, decisions)
        saved = state_manager.load(review_dir)
        assert saved.status == ReviewStatus.abstract_screening

    def test_status_not_updated_before_completion(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid="1"), _make_article(pmid="2")])
        decisions = [{"article_index": 0, "decision": "include", "reason": "OK", "reason_category": None}]
        _capture_save(capsys, review_dir, 0, decisions)
        saved = state_manager.load(review_dir)
        assert saved.status == ReviewStatus.deduplicated

    def test_completion_summary_includes_counts(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [
            _make_article(pmid="1"),
            _make_article(pmid="2"),
            _make_article(pmid="3"),
        ])
        decisions = [
            {"article_index": 0, "decision": "include", "reason": "OK", "reason_category": None},
            {"article_index": 1, "decision": "exclude", "reason": "Wrong design", "reason_category": "wrong_study_design"},
            {"article_index": 2, "decision": "borderline", "reason": "Ambiguous", "reason_category": None},
        ]
        out = _capture_save(capsys, review_dir, 0, decisions)
        assert out["include"] == 1
        assert out["exclude"] == 1
        assert out["borderline"] == 1

    def test_null_reason_category_defaults_to_other_in_prisma(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        _write_deduped(review_dir, [_make_article(pmid="1")])
        decisions = [{"article_index": 0, "decision": "exclude", "reason": "Misc", "reason_category": None}]
        _capture_save(capsys, review_dir, 0, decisions)
        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.excluded_screening.get("other", 0) == 1


# ---------------------------------------------------------------------------
# Batch accumulation across multiple saves
# ---------------------------------------------------------------------------

class TestBatchAccumulation:
    def test_two_batches_accumulate_decisions(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(pmid=str(i), title=f"Study {i}") for i in range(4)]
        _write_deduped(review_dir, articles)

        # Batch 1: articles 0–1
        decisions_b1 = [
            {"article_index": 0, "decision": "include", "reason": "OK", "reason_category": None},
            {"article_index": 1, "decision": "exclude", "reason": "Wrong pop", "reason_category": "wrong_population"},
        ]
        out1 = _capture_save(capsys, review_dir, 0, decisions_b1)
        assert out1["complete"] is False
        assert out1["pending_remaining"] == 2

        # Batch 2: articles 2–3
        decisions_b2 = [
            {"article_index": 2, "decision": "include", "reason": "Relevant", "reason_category": None},
            {"article_index": 3, "decision": "borderline", "reason": "Unclear", "reason_category": None},
        ]
        out2 = _capture_save(capsys, review_dir, 2, decisions_b2)
        assert out2["complete"] is True

    def test_earlier_decisions_preserved_across_saves(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(pmid=str(i)) for i in range(4)]
        _write_deduped(review_dir, articles)

        # First batch
        _capture_save(capsys, review_dir, 0, [
            {"article_index": 0, "decision": "include", "reason": "OK", "reason_category": None},
            {"article_index": 1, "decision": "exclude", "reason": "Wrong design", "reason_category": "wrong_study_design"},
        ])

        # Second batch finalizes
        _capture_save(capsys, review_dir, 2, [
            {"article_index": 2, "decision": "include", "reason": "OK", "reason_category": None},
            {"article_index": 3, "decision": "include", "reason": "OK", "reason_category": None},
        ])

        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert screened[0]["screening_decision"] == "include"
        assert screened[1]["screening_decision"] == "exclude"
        assert screened[2]["screening_decision"] == "include"
        assert screened[3]["screening_decision"] == "include"

    def test_prisma_counts_accumulate_correctly_across_batches(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        articles = [_make_article(pmid=str(i)) for i in range(6)]
        _write_deduped(review_dir, articles)

        _capture_save(capsys, review_dir, 0, [
            {"article_index": 0, "decision": "include", "reason": "OK", "reason_category": None},
            {"article_index": 1, "decision": "exclude", "reason": "Pediatric", "reason_category": "wrong_population"},
            {"article_index": 2, "decision": "exclude", "reason": "Pediatric", "reason_category": "wrong_population"},
        ])

        _capture_save(capsys, review_dir, 3, [
            {"article_index": 3, "decision": "include", "reason": "OK", "reason_category": None},
            {"article_index": 4, "decision": "exclude", "reason": "No RCT", "reason_category": "wrong_study_design"},
            {"article_index": 5, "decision": "borderline", "reason": "Unclear", "reason_category": None},
        ])

        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.screened == 6
        assert saved.prisma_counts.excluded_screening["wrong_population"] == 2
        assert saved.prisma_counts.excluded_screening["wrong_study_design"] == 1
