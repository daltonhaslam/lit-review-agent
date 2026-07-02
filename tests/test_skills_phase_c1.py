"""Phase C1 tests: --prepare/--save pattern for Stages 1–4 skills.

No LLM calls — all skills are pure data in/out after conversion.
PubMedClient.mesh_lookup is mocked for query_builder --prepare tests.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agent import state as state_manager
from src.models.pico import PICO, FundamentalFour
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills import pico_refiner, protocol_generator, query_builder, review_type_classifier
from src.skills.protocol_generator import ProtocolLockedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-review-2026-04"
    review_dir.mkdir()
    review = Review(
        review_id="test-review-2026-04",
        raw_question="Does early mobility reduce hospital LOS in ICU patients?",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        **kwargs,
    )
    state_manager.save(review, review_dir)
    return review, review_dir


def _make_pico() -> PICO:
    return PICO(
        P="Adult ICU patients",
        I="Early mobility protocol",
        C="Standard bed rest",
        O=["Hospital length of stay", "ICU length of stay"],
        S=None,
        inclusion_criteria=["Adults ≥18 years", "ICU admission"],
        exclusion_criteria=["Paediatric patients", "Palliative care"],
        fundamental_four=FundamentalFour(
            what_we_know="Immobility causes deconditioning",
            what_we_dont_know="Optimal timing and intensity",
            what_we_should_know="Effect on LOS and functional outcomes",
            why_we_should_know="Reduce cost and improve recovery",
        ),
        raw_question="Does early mobility reduce hospital LOS in ICU patients?",
    )


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
    """Read stdout and return parsed JSON."""
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# review_type_classifier
# ---------------------------------------------------------------------------

class TestReviewTypeClassifierPrepare:
    def test_prepare_returns_valid_json(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        review_type_classifier.prepare(review_dir)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["question"] == "Does early mobility reduce hospital LOS in ICU patients?"
        assert "review_type_options" in data
        assert "systematic" in data["review_type_options"]
        assert "task" in data

    def test_prepare_includes_all_six_types(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        review_type_classifier.prepare(review_dir)
        data = _capture_json(capsys)
        for rt in ("narrative", "scoping", "systematic", "umbrella", "realist", "critical"):
            assert rt in data["review_type_options"]

    def test_prepare_includes_depth_by_type(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        review_type_classifier.prepare(review_dir)
        data = _capture_json(capsys)
        assert "depth_by_type" in data
        assert data["depth_by_type"]["systematic"] == "exhaustive"


class TestReviewTypeClassifierSave:
    def test_save_writes_state(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        review_type_classifier.save({"review_type": "scoping", "rationale": "Exploratory."}, review_dir)
        review = state_manager.load(review_dir)
        assert review.review_type == ReviewType.scoping
        assert review.status == ReviewStatus.review_type_classified

    def test_save_prints_confirmation_json(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        review_type_classifier.save({"review_type": "narrative", "rationale": "Quick summary needed."}, review_dir)
        data = _capture_json(capsys)
        assert data["review_type"] == "narrative"
        assert data["status"] == ReviewStatus.review_type_classified.value

    def test_save_invalid_type_falls_back_to_narrative(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        review_type_classifier.save({"review_type": "bogus_type", "rationale": ""}, review_dir)
        review = state_manager.load(review_dir)
        assert review.review_type == ReviewType.narrative

    def test_save_all_valid_types(self, tmp_path):
        for rt in ("narrative", "scoping", "systematic", "umbrella", "realist", "critical"):
            review_dir = tmp_path / f"review-{rt}"
            review_dir.mkdir()
            review = Review(
                review_id=f"review-{rt}",
                raw_question="Test question",
                screening_mode=ScreeningMode.thorough,
            )
            state_manager.save(review, review_dir)
            review_type_classifier.save({"review_type": rt, "rationale": ""}, review_dir)
            saved = state_manager.load(review_dir)
            assert saved.review_type == ReviewType(rt)


# ---------------------------------------------------------------------------
# pico_refiner
# ---------------------------------------------------------------------------

ANSWERS = {
    "what_we_know": "Immobility causes deconditioning.",
    "what_we_dont_know": "Optimal timing.",
    "what_we_should_know": "Effect on LOS.",
    "why_we_should_know": "Reduce costs.",
}

PICO_JSON = {
    "pico": {
        "P": "Adult ICU patients",
        "I": "Early mobility",
        "C": "Standard care",
        "O": ["Hospital LOS", "ICU LOS"],
        "S": None,
    },
    "inclusion_criteria": ["Adults ≥18"],
    "exclusion_criteria": ["Paediatric"],
    "fundamental_four": {
        "what_we_know": "Immobility causes deconditioning.",
        "what_we_dont_know": "Optimal timing.",
        "what_we_should_know": "Effect on LOS.",
        "why_we_should_know": "Reduce costs.",
    },
}


class TestPicoRefinerPrepare:
    def test_prepare_returns_valid_json(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        pico_refiner.prepare(ANSWERS, review_dir)
        data = _capture_json(capsys)
        assert data["question"] == "Does early mobility reduce hospital LOS in ICU patients?"
        assert data["review_type"] == "systematic"
        assert data["fundamental_four"]["what_we_know"] == ANSWERS["what_we_know"]
        assert "task" in data

    def test_prepare_includes_all_four_answers(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        pico_refiner.prepare(ANSWERS, review_dir)
        data = _capture_json(capsys)
        ff = data["fundamental_four"]
        for key in ("what_we_know", "what_we_dont_know", "what_we_should_know", "why_we_should_know"):
            assert key in ff

    def test_prepare_empty_answers_still_valid(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        pico_refiner.prepare({}, review_dir)
        data = _capture_json(capsys)
        assert data["fundamental_four"]["what_we_know"] == ""


class TestPicoRefinerSave:
    def test_save_writes_pico_to_state(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        pico_refiner.save(PICO_JSON, review_dir)
        review = state_manager.load(review_dir)
        assert review.pico is not None
        assert review.pico.P == "Adult ICU patients"
        assert review.pico.I == "Early mobility"
        assert review.status == ReviewStatus.pico_formulated

    def test_save_outcomes_is_list(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        pico_refiner.save(PICO_JSON, review_dir)
        review = state_manager.load(review_dir)
        assert isinstance(review.pico.O, list)

    def test_save_coerces_string_O_to_list(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        data = dict(PICO_JSON)
        data["pico"] = dict(PICO_JSON["pico"])
        data["pico"]["O"] = "Single outcome as string"
        pico_refiner.save(data, review_dir)
        review = state_manager.load(review_dir)
        assert review.pico.O == ["Single outcome as string"]

    def test_save_writes_inclusion_exclusion_criteria(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        pico_refiner.save(PICO_JSON, review_dir)
        review = state_manager.load(review_dir)
        assert review.pico.inclusion_criteria == ["Adults ≥18"]
        assert review.pico.exclusion_criteria == ["Paediatric"]

    def test_save_fundamental_four_persisted(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path)
        pico_refiner.save(PICO_JSON, review_dir)
        review = state_manager.load(review_dir)
        assert review.pico.fundamental_four.what_we_know == "Immobility causes deconditioning."


# ---------------------------------------------------------------------------
# protocol_generator
# ---------------------------------------------------------------------------

class TestProtocolGeneratorPrepare:
    def test_prepare_returns_valid_json(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        protocol_generator.prepare(review_dir)
        data = _capture_json(capsys)
        assert data["review_id"] == "test-review-2026-04"
        assert data["pico"]["P"] == "Adult ICU patients"
        assert "protocol_structure" in data
        assert "task" in data

    def test_prepare_includes_criteria(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        protocol_generator.prepare(review_dir)
        data = _capture_json(capsys)
        assert "Adults ≥18 years" in data["inclusion_criteria"]
        assert "Paediatric patients" in data["exclusion_criteria"]

    def test_prepare_raises_when_pico_missing(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        with pytest.raises(ValueError, match="PICO must be formulated"):
            protocol_generator.prepare(review_dir)

    def test_prepare_raises_when_already_locked(self, tmp_path):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        review = state_manager.load(review_dir)
        review.protocol_locked_at = datetime.utcnow()
        state_manager.save(review, review_dir)
        with pytest.raises(ProtocolLockedError):
            protocol_generator.prepare(review_dir)


class TestProtocolGeneratorSave:
    PROTOCOL_TEXT = "# Protocol: Early Mobility in ICU\n\n## 1. Background\nTest content."

    def test_save_writes_protocol_file(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        protocol_generator.save({"protocol_text": self.PROTOCOL_TEXT}, review_dir)
        assert (review_dir / "protocol.md").read_text() == self.PROTOCOL_TEXT

    def test_save_locks_state(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        protocol_generator.save({"protocol_text": self.PROTOCOL_TEXT}, review_dir)
        review = state_manager.load(review_dir)
        assert review.protocol_locked_at is not None
        assert review.status == ReviewStatus.protocol_generated

    def test_save_prints_summary_json(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        protocol_generator.save({"protocol_text": self.PROTOCOL_TEXT}, review_dir)
        data = _capture_json(capsys)
        assert "protocol_file" in data
        assert "protocol_locked_at" in data
        assert isinstance(data["word_count"], int)

    def test_save_raises_when_already_locked(self, tmp_path):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        review = state_manager.load(review_dir)
        review.protocol_locked_at = datetime.utcnow()
        review.status = ReviewStatus.protocol_generated
        state_manager.save(review, review_dir)
        with pytest.raises(ProtocolLockedError):
            protocol_generator.save({"protocol_text": self.PROTOCOL_TEXT}, review_dir)


# ---------------------------------------------------------------------------
# query_builder
# ---------------------------------------------------------------------------

QUERY_JSON = {
    "pubmed_steps": [
        {"step": "#1", "query": '"ICU Patients"[Mesh]', "rationale": "Population — MeSH"},
        {"step": "#2", "query": "icu*[tiab] OR intensive care*[tiab]", "rationale": "Population — free-text"},
        {"step": "#3", "query": "#1 OR #2", "rationale": "Population — MeSH OR free-text"},
        {"step": "#4", "query": "(#3) AND Humans[Mesh] AND English[lang]", "rationale": "Final"},
    ],
    "openalex_query": "early mobility ICU length of stay",
    "semantic_scholar_query": "ICU early mobilization hospital length of stay",
}


class TestQueryBuilderPrepare:
    def _run_prepare(self, review_dir: Path) -> dict:
        return asyncio.run(query_builder._run_prepare(review_dir))

    def test_prepare_calls_mesh_lookup(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("early mobility"))
            asyncio.run(query_builder._run_prepare(review_dir))
        assert instance.mesh_lookup.call_count >= 1

    def test_prepare_outputs_valid_json(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("mobility"))
            asyncio.run(query_builder._run_prepare(review_dir))
        data = _capture_json(capsys)
        assert "pico" in data
        assert "pubmed_steps_from_mesh" in data
        assert "mesh_warnings" in data
        assert "task" in data

    def test_prepare_produces_pubmed_steps(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_hit("mobility"))
            asyncio.run(query_builder._run_prepare(review_dir))
        data = _capture_json(capsys)
        assert len(data["pubmed_steps_from_mesh"]) > 0
        assert all("step" in s and "query" in s for s in data["pubmed_steps_from_mesh"])

    def test_prepare_mesh_miss_produces_warning(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        with patch("src.skills.query_builder.PubMedClient") as MockClient:
            instance = MockClient.return_value
            instance.mesh_lookup = AsyncMock(return_value=_mesh_miss("early mobility"))
            asyncio.run(query_builder._run_prepare(review_dir))
        data = _capture_json(capsys)
        warnings = data["mesh_warnings"]
        assert any("free-text" in w for w in warnings)

    def test_prepare_raises_when_pico_missing(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        with pytest.raises(ValueError, match="PICO must be formulated"):
            asyncio.run(query_builder._run_prepare(review_dir))


class TestQueryBuilderSave:
    def test_save_writes_queryset(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        query_builder.save(QUERY_JSON, review_dir)
        review = state_manager.load(review_dir)
        assert review.queries is not None
        assert len(review.queries.pubmed) == 4
        assert review.queries.openalex[0].query_string == "early mobility ICU length of stay"

    def test_save_sets_status(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        query_builder.save(QUERY_JSON, review_dir)
        review = state_manager.load(review_dir)
        assert review.status == ReviewStatus.queries_built

    def test_save_prints_confirmation_json(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        query_builder.save(QUERY_JSON, review_dir)
        data = _capture_json(capsys)
        assert data["status"] == ReviewStatus.queries_built.value
        assert data["pubmed_steps_count"] == 4

    def test_save_empty_cross_db_queries_gives_empty_lists(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        data = dict(QUERY_JSON)
        data["openalex_query"] = ""
        data["semantic_scholar_query"] = ""
        query_builder.save(data, review_dir)
        review = state_manager.load(review_dir)
        assert review.queries.openalex == []
        assert review.queries.semantic_scholar == []

    def test_save_missing_cross_db_keys_gives_empty_lists(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        query_builder.save({"pubmed_steps": QUERY_JSON["pubmed_steps"]}, review_dir)
        review = state_manager.load(review_dir)
        assert review.queries.openalex == []
        assert review.queries.semantic_scholar == []


# ---------------------------------------------------------------------------
# _steps_from_mesh pure function
# ---------------------------------------------------------------------------

class TestStepsFromMesh:
    def test_mesh_hit_produces_three_steps(self):
        steps, warnings, next_num = query_builder._steps_from_mesh(
            _mesh_hit("heart failure"), "heart failure", "Population (P)", 1, 2026
        )
        assert len(steps) == 3
        assert next_num == 4
        assert warnings == []

    def test_mesh_miss_produces_one_step(self):
        steps, warnings, next_num = query_builder._steps_from_mesh(
            _mesh_miss("telemonitoring"), "telemonitoring", "Intervention (I)", 1, 2026
        )
        assert len(steps) == 1
        assert next_num == 2
        assert any("free-text" in w for w in warnings)

    def test_new_mesh_term_produces_warning(self):
        mesh = {"found": True, "preferred_name": "NewTerm", "entry_terms": [], "year_introduced": 2023}
        _, warnings, _ = query_builder._steps_from_mesh(mesh, "newterm", "P", 1, 2026)
        assert any("2023" in w for w in warnings)

    def test_step_numbering_continues(self):
        steps, _, next_num = query_builder._steps_from_mesh(
            _mesh_hit("diabetes"), "diabetes", "Population (P)", 5, 2026
        )
        assert steps[0]["step"] == "#5"
        assert steps[-1]["step"] == "#7"
        assert next_num == 8


# ---------------------------------------------------------------------------
# _flatten_query pure function
# ---------------------------------------------------------------------------

class TestFlattenQuery:
    def test_no_refs_passthrough(self):
        steps = [
            {"step": "#1", "query": '"Heart Failure"[Mesh]'},
            {"step": "#2", "query": "heart failure*[tiab]"},
            {"step": "#3", "query": '"Heart Failure"[Mesh] AND Humans[Mesh]'},
        ]
        result, _ = query_builder._flatten_and_clean(steps)
        assert result == '"Heart Failure"[Mesh] AND Humans[Mesh]'

    def test_single_level_ref(self):
        steps = [
            {"step": "#1", "query": '"Heart Failure"[Mesh]'},
            {"step": "#2", "query": "heart failure*[tiab]"},
            {"step": "#3", "query": "#1 OR #2"},
            {"step": "#4", "query": "(#3) AND Humans[Mesh]"},
        ]
        result, _ = query_builder._flatten_and_clean(steps)
        assert "#3" not in result
        assert "#1" not in result
        assert "#2" not in result
        assert "Humans[Mesh]" in result
        assert "Heart Failure" in result
        assert "heart failure" in result

    def test_two_level_refs(self):
        steps = [
            {"step": "#1", "query": '"HF"[Mesh]'},
            {"step": "#2", "query": "hf*[tiab]"},
            {"step": "#3", "query": "#1 OR #2"},
            {"step": "#4", "query": '"Mobility"[Mesh]'},
            {"step": "#5", "query": "mobility*[tiab]"},
            {"step": "#6", "query": "#4 OR #5"},
            {"step": "#7", "query": "(#3 AND #6) AND Humans[Mesh]"},
        ]
        result, _ = query_builder._flatten_and_clean(steps)
        for ref in ("#1", "#2", "#3", "#4", "#5", "#6"):
            assert ref not in result
        assert "HF" in result
        assert "Mobility" in result
        assert "Humans[Mesh]" in result

    def test_single_step_returns_its_query(self):
        steps = [{"step": "#1", "query": "heart failure readmission"}]
        result, _ = query_builder._flatten_and_clean(steps)
        assert result == "heart failure readmission"

    def test_save_flattens_final_pubmed_step(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        data = {
            "pubmed_steps": [
                {"step": "#1", "query": '"ICU"[Mesh]', "rationale": "P MeSH"},
                {"step": "#2", "query": "icu*[tiab]", "rationale": "P free-text"},
                {"step": "#3", "query": "#1 OR #2", "rationale": "P combined"},
                {"step": "#4", "query": "(#3) AND Humans[Mesh]", "rationale": "Final"},
            ],
            "openalex_query": "ICU early mobility",
            "semantic_scholar_query": "ICU mobilization",
            "europepmc_query": "ICU early mobility hospital",
        }
        query_builder.save(data, review_dir)
        review = state_manager.load(review_dir)
        final_q = review.queries.pubmed[-1].query_string
        assert "#3" not in final_q
        assert "#1" not in final_q
        assert "ICU" in final_q
        assert "Humans[Mesh]" in final_q


# ---------------------------------------------------------------------------
# europepmc_query in query_builder.save
# ---------------------------------------------------------------------------

class TestQueryBuilderEuropePMCQuery:
    def test_save_stores_europepmc_query(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        data = dict(QUERY_JSON)
        data["europepmc_query"] = "early mobility intensive care unit length of stay"
        query_builder.save(data, review_dir)
        review = state_manager.load(review_dir)
        assert len(review.queries.europepmc) == 1
        assert review.queries.europepmc[0].query_string == "early mobility intensive care unit length of stay"

    def test_save_without_europepmc_query_gives_empty_list(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        query_builder.save(QUERY_JSON, review_dir)
        review = state_manager.load(review_dir)
        assert review.queries.europepmc == []

    def test_save_empty_europepmc_query_gives_empty_list(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, pico=_make_pico())
        data = dict(QUERY_JSON)
        data["europepmc_query"] = ""
        query_builder.save(data, review_dir)
        review = state_manager.load(review_dir)
        assert review.queries.europepmc == []

    def test_query_prompt_includes_europepmc(self):
        assert "europepmc_query" in query_builder.QUERY_PROMPT
        assert "europepmc" in query_builder.QUERY_PROMPT.lower()
