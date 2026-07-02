"""Phase F2 tests: query_builder robustness — Issues #21 and #22a.

Covers:
  - Long PICO fields skip MeSH lookup; skipped_fields emitted (Issue #21)
  - Short PICO fields still call MeSH lookup normally
  - _flatten_and_clean: stray char stripping and unbalanced-paren warning (Issue #22a)
  - save() merges flatten_warnings into mesh_warnings output
  - End-to-end regression with the pediatric-observation PICO that triggered the bug
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agent import state as state_manager
from src.models.pico import PICO, FundamentalFour
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills import query_builder
from src.skills.query_builder import _flatten_and_clean


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_review(tmp_path: Path, pico: PICO | None = None) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-f2-2026-05"
    review_dir.mkdir()
    review = Review(
        review_id="test-f2-2026-05",
        raw_question="Test question",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        pico=pico,
    )
    state_manager.save(review, review_dir)
    return review, review_dir


def _short_pico() -> PICO:
    return PICO(
        P="hypertension",
        I="lisinopril",
        C="placebo",
        O=["mortality"],
    )


def _long_pico() -> PICO:
    return PICO(
        P="Pediatric patients (age < 18) hospitalized at US children's hospitals or pediatric units",
        I="Observation status hospitalization (outpatient observation classification under US payer and CMS regulatory framework)",
        C="Inpatient admission status — compared on resource utilization, acuity markers, cost, and reimbursement",
        O=["Trend in observation hospitalization rate over time"],
    )


def _mesh_hit(term: str) -> dict:
    return {
        "found": True,
        "preferred_name": term.title(),
        "entry_terms": [f"{term} entry"],
        "year_introduced": 2000,
    }


def _mesh_miss(term: str) -> dict:
    return {"found": False, "term": term}


def _capture_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# Issue #21 — long fields use concept extraction + MeSH lookup (not skipped_fields)
# ---------------------------------------------------------------------------

class TestLongFieldSkipsMeshLookup:
    def test_long_p_field_uses_concept_extraction(self, tmp_path, capsys):
        """Long fields now extract concepts and run MeSH lookups — not unconditionally skipped."""
        _, review_dir = _make_review(tmp_path, pico=_long_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("observation"))
            asyncio.run(query_builder._run_prepare(review_dir))
        # Concepts extracted from long fields → mesh_lookup IS called for them
        assert instance.mesh_lookup.call_count > 0

    def test_long_p_field_not_in_skipped_fields_when_concepts_found(self, tmp_path, capsys):
        """Long fields with extractable concepts go through MeSH, not skipped_fields."""
        _, review_dir = _make_review(tmp_path, pico=_long_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("observation"))
            asyncio.run(query_builder._run_prepare(review_dir))
        data = _capture_json(capsys)
        labels = [sf["label"] for sf in data.get("skipped_fields", [])]
        assert "Population (P)" not in labels

    def test_long_field_warning_mentions_extracted_concepts(self, tmp_path, capsys):
        """Warning for long fields should cite the extracted concepts, not just 'skipped'."""
        _, review_dir = _make_review(tmp_path, pico=_long_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("observation"))
            asyncio.run(query_builder._run_prepare(review_dir))
        data = _capture_json(capsys)
        assert any("extracted concepts" in w for w in data["mesh_warnings"])

    def test_long_field_skipped_fields_empty_when_concepts_found(self, tmp_path, capsys):
        """skipped_fields should be empty when concept extraction succeeds for all long fields."""
        _, review_dir = _make_review(tmp_path, pico=_long_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("x"))
            asyncio.run(query_builder._run_prepare(review_dir))
        data = _capture_json(capsys)
        assert data.get("skipped_fields", []) == []

    def test_short_p_field_calls_mesh_lookup(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_short_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("hypertension"))
            asyncio.run(query_builder._run_prepare(review_dir))
        # P="hypertension" (1 word), I="lisinopril" (1 word), C="placebo" (1 word),
        # O=["mortality"] (1 word) — all short, all should be looked up.
        assert instance.mesh_lookup.call_count >= 3

    def test_short_pico_has_empty_skipped_fields(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_short_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("hypertension"))
            asyncio.run(query_builder._run_prepare(review_dir))
        data = _capture_json(capsys)
        assert data["skipped_fields"] == []

    def test_mixed_pico_long_field_uses_extraction_not_skip(self, tmp_path, capsys):
        """Short P uses direct MeSH; long I uses concept extraction — neither ends in skipped_fields."""
        mixed = PICO(
            P="hypertension",
            I="Observation status hospitalization (outpatient observation classification under US payer and CMS)",
            C="placebo",
            O=["mortality"],
        )
        _, review_dir = _make_review(tmp_path, pico=mixed)
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("hypertension"))
            asyncio.run(query_builder._run_prepare(review_dir))
        data = _capture_json(capsys)
        # Long I field extracts concepts → not in skipped_fields
        skipped_labels = [sf["label"] for sf in data.get("skipped_fields", [])]
        assert "Intervention (I)" not in skipped_labels
        assert "Population (P)" not in skipped_labels


# ---------------------------------------------------------------------------
# Issue #22a — _flatten_and_clean validation
# ---------------------------------------------------------------------------

class TestFlattenAndClean:
    def _make_steps(self, queries: list[str]) -> list[dict]:
        return [{"step": f"#{i+1}", "query": q} for i, q in enumerate(queries)]

    def test_stray_char_between_close_parens_stripped(self):
        # Pattern matches )X) where X is a non-operator char (not alpha/paren/space).
        # "A)0)B" has the )0) sequence that should be stripped to ))
        steps = [{"step": "#1", "query": "A)0)B"}]
        flat, warnings = _flatten_and_clean(steps)
        assert any("Stripped stray '0'" in w for w in warnings)
        assert "0" not in flat

    def test_unbalanced_parens_warning_emitted(self):
        # Single step with unclosed paren — no #N refs so the imbalance survives to the check.
        steps = [{"step": "#1", "query": "(A OR B"}]
        flat, warnings = _flatten_and_clean(steps)
        assert any("unbalanced parentheses" in w for w in warnings)

    def test_unbalanced_parens_query_not_truncated(self):
        """Query is left as-is even when unbalanced — user must review."""
        steps = [{"step": "#1", "query": "(A OR B"}]
        flat, warnings = _flatten_and_clean(steps)
        # Content is preserved; only a warning is added
        assert "A OR B" in flat

    def test_balanced_clean_query_no_warnings(self):
        steps = self._make_steps([
            '"Heart Failure"[Mesh]',
            "heart failure*[tiab]",
            "#1 OR #2",
            "(#3) AND Humans[Mesh] AND English[lang]",
        ])
        flat, warnings = _flatten_and_clean(steps)
        assert warnings == []
        assert flat.count("(") == flat.count(")")


# ---------------------------------------------------------------------------
# Issue #21 end-to-end regression — pediatric PICO
# ---------------------------------------------------------------------------

class TestPediatricPICORegression:
    PEDIATRIC_P = (
        "Pediatric patients (age < 18) hospitalized at US children's hospitals or pediatric units, "
        "including all-cause admissions and specific high-acuity diagnoses (e.g., acute respiratory "
        "failure, conditions requiring ICU-level care)"
    )
    PEDIATRIC_I = (
        "Observation status hospitalization (outpatient observation classification under US payer "
        "and CMS regulatory framework)"
    )
    PEDIATRIC_C = (
        "Inpatient admission status — compared on resource utilization, acuity markers, cost, "
        "and reimbursement"
    )

    def _pediatric_pico(self) -> PICO:
        return PICO(
            P=self.PEDIATRIC_P,
            I=self.PEDIATRIC_I,
            C=self.PEDIATRIC_C,
            O=["Trend in observation hospitalization rate over time (2010–2025)"],
        )

    def test_pediatric_pico_long_fields_use_extraction(self, tmp_path, capsys):
        """Pediatric PICO has all-long fields; concept extraction runs, skipped_fields empty."""
        _, review_dir = _make_review(tmp_path, pico=self._pediatric_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("x"))
            asyncio.run(query_builder._run_prepare(review_dir))
        data = _capture_json(capsys)
        # Concept extraction succeeds for long fields → none in skipped_fields
        skipped_labels = {sf["label"] for sf in data.get("skipped_fields", [])}
        assert "Population (P)" not in skipped_labels
        assert "Intervention (I)" not in skipped_labels
        assert "Comparator (C)" not in skipped_labels

    def test_pediatric_pico_mesh_called_for_concepts(self, tmp_path, capsys):
        """Concept extraction finds terms in long fields → mesh_lookup IS called."""
        _, review_dir = _make_review(tmp_path, pico=self._pediatric_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("x"))
            asyncio.run(query_builder._run_prepare(review_dir))
        assert instance.mesh_lookup.call_count > 0

    def test_pediatric_pico_output_has_task_prompt(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=self._pediatric_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("x"))
            asyncio.run(query_builder._run_prepare(review_dir))
        data = _capture_json(capsys)
        assert "skipped_fields" in data["task"]
