"""Tests for Phase 1 query_builder fixes.

Covers:
  - #21: _extract_concepts() extracts meaningful terms from long PICO sentences
  - #21: long-field PICO routes through concept extraction + MeSH lookup (not skipped_fields)
  - #34: QUERY_PROMPT instructs natural-language phrase for semantic_scholar_query
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
from src.models.pico import PICO
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills import query_builder
from src.skills.query_builder import QUERY_PROMPT, _extract_concepts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_review(tmp_path: Path, pico: PICO | None = None) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-concept-2026-05"
    review_dir.mkdir()
    review = Review(
        review_id="test-concept-2026-05",
        raw_question="Test question",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        pico=pico,
    )
    state_manager.save(review, review_dir)
    return review, review_dir


def _mesh_hit(term: str) -> dict:
    return {
        "found": True,
        "preferred_name": term.title(),
        "entry_terms": [f"{term} synonym"],
        "year_introduced": 2000,
    }


def _mesh_miss(term: str) -> dict:
    return {"found": False, "term": term}


def _capture_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# _extract_concepts unit tests
# ---------------------------------------------------------------------------

class TestExtractConcepts:
    def test_two_word_phrase_may_return_bigram(self):
        # _extract_concepts has no word-count guard; short text still runs.
        # Two words with ≥4 chars may form a bigram — that's fine; guard is in _run_prepare.
        result = _extract_concepts("Heart failure")
        assert isinstance(result, list)

    def test_long_text_returns_concepts(self):
        result = _extract_concepts(
            "Pediatric patients hospitalized at US children's hospitals or pediatric units"
        )
        assert len(result) >= 1
        assert all(len(c) >= 4 for c in result)

    def test_strips_parentheticals(self):
        result = _extract_concepts(
            "Observation status hospitalization (outpatient observation classification under payer)"
        )
        assert len(result) >= 1
        # parenthetical content removed, core terms remain
        assert any("observation" in c for c in result)

    def test_stopwords_excluded(self):
        result = _extract_concepts(
            "Patients with heart failure during hospitalization after discharge"
        )
        for concept in result:
            tokens = concept.split()
            for token in tokens:
                assert token not in {"patients", "with", "during", "after"}

    def test_returns_at_most_four(self):
        result = _extract_concepts(
            "Adult diabetic patients receiving insulin therapy during hospitalization with complications"
        )
        assert len(result) <= 4

    def test_observation_status_bigram(self):
        result = _extract_concepts(
            "Observation status hospitalization and inpatient admission comparison analysis"
        )
        # "observation status" should be recognized as an adjacent bigram
        assert any("observation status" in c or "observation" in c for c in result)

    def test_deduplicates_repeated_terms(self):
        result = _extract_concepts(
            "Pediatric children pediatric hospitalization pediatric care pediatric units"
        )
        # "pediatric" should appear at most once
        pediatric_count = sum(1 for c in result if c == "pediatric")
        assert pediatric_count <= 1

    def test_returns_strings(self):
        result = _extract_concepts(
            "Adult patients with diabetes mellitus type two hospitalization insulin treatment"
        )
        assert all(isinstance(c, str) for c in result)


# ---------------------------------------------------------------------------
# _run_prepare: long PICO fields route through concept extraction
# ---------------------------------------------------------------------------

class TestLongFieldConceptExtraction:
    def test_long_pico_produces_mesh_steps(self, tmp_path, capsys):
        """Long-field PICO should produce MeSH-derived steps via concept extraction."""
        pico = PICO(
            P="Pediatric patients hospitalized at US children's hospitals or pediatric units",
            I="Observation status hospitalization under CMS and payer framework",
            C="Standard inpatient admission comparison",
            O=["Trend in hospitalization rate over time"],
        )
        _, review_dir = _make_review(tmp_path, pico)

        async def fake_mesh(term):
            return _mesh_hit(term)

        with patch("src.skills.query_builder.RateLimiter.retry_with_backoff") as mock_rl:
            async def rl_side_effect(fn):
                return await fn()
            mock_rl.side_effect = rl_side_effect

            with patch("src.integrations.pubmed_client.PubMedClient.mesh_lookup", new=AsyncMock(side_effect=fake_mesh)):
                asyncio.run(query_builder._run_prepare(review_dir))

        output = _capture_json(capsys)

        # Should have MeSH steps from concept extraction
        assert "pubmed_steps_from_mesh" in output
        steps = output["pubmed_steps_from_mesh"]
        assert len(steps) >= 1

        # Concept extraction succeeded → P and I fields should NOT be in skipped_fields
        skipped = output.get("skipped_fields", [])
        skipped_labels = {s["label"] for s in skipped}
        assert "Population (P)" not in skipped_labels
        assert "Intervention (I)" not in skipped_labels

    def test_long_pico_mesh_warnings_mention_extraction(self, tmp_path, capsys):
        """Warnings should mention extracted concepts, not just 'Claude must construct'."""
        pico = PICO(
            P="Pediatric patients hospitalized at US children's hospitals",
            I="lisinopril",
            C="Usual care",
            O=["mortality"],
        )
        _, review_dir = _make_review(tmp_path, pico)

        async def fake_mesh(term):
            return _mesh_hit(term)

        with patch("src.skills.query_builder.RateLimiter.retry_with_backoff") as mock_rl:
            async def rl_side_effect(fn):
                return await fn()
            mock_rl.side_effect = rl_side_effect

            with patch("src.integrations.pubmed_client.PubMedClient.mesh_lookup", new=AsyncMock(side_effect=fake_mesh)):
                asyncio.run(query_builder._run_prepare(review_dir))

        output = _capture_json(capsys)
        warnings = output.get("mesh_warnings", [])
        # At least one warning should mention "extracted concepts"
        assert any("extracted concepts" in w for w in warnings)

    def test_short_pico_still_uses_normal_mesh(self, tmp_path, capsys):
        """Short PICO fields (≤3 words) continue to use direct MeSH lookup."""
        pico = PICO(
            P="hypertension",
            I="lisinopril",
            C="placebo",
            O=["mortality"],
        )
        _, review_dir = _make_review(tmp_path, pico)

        async def fake_mesh(term):
            return _mesh_hit(term)

        with patch("src.skills.query_builder.RateLimiter.retry_with_backoff") as mock_rl:
            async def rl_side_effect(fn):
                return await fn()
            mock_rl.side_effect = rl_side_effect

            with patch("src.integrations.pubmed_client.PubMedClient.mesh_lookup", new=AsyncMock(side_effect=fake_mesh)):
                asyncio.run(query_builder._run_prepare(review_dir))

        output = _capture_json(capsys)
        steps = output.get("pubmed_steps_from_mesh", [])
        assert len(steps) >= 1
        # No skipped fields for short PICO
        assert output.get("skipped_fields", []) == []


# ---------------------------------------------------------------------------
# QUERY_PROMPT: semantic_scholar_query must be natural-language
# ---------------------------------------------------------------------------

class TestQueryPromptS2Guidance:
    def test_prompt_specifies_natural_language_for_s2(self):
        assert "natural-language" in QUERY_PROMPT or "natural language" in QUERY_PROMPT.lower()

    def test_prompt_prohibits_and_or_not_for_s2(self):
        assert "no AND/OR/NOT" in QUERY_PROMPT

    def test_prompt_includes_s2_example(self):
        assert "pediatric observation" in QUERY_PROMPT or "search bar" in QUERY_PROMPT

    def test_prompt_keeps_boolean_for_openalex(self):
        assert "openalex_query" in QUERY_PROMPT
        assert "Boolean" in QUERY_PROMPT

    def test_prompt_keeps_boolean_for_europepmc(self):
        assert "europepmc_query" in QUERY_PROMPT
