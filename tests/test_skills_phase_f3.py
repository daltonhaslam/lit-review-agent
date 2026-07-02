"""Unit tests for Phase F3 — citation chaser fixes.

Issues closed: #29 (abstract enrichment), #30 (s2_id seed enrichment + S2 key warning),
#31 (borderlines preserved in save), plus forward-chase parallelization.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.citation_chaser import (
    _CANDIDATES_FILE as CC_CANDIDATES_FILE,
    _enrich_abstracts,
    _enrich_seeds_s2,
    _forward_chase,
    save as cc_save,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pico(**kwargs) -> PICO:
    defaults = dict(
        P="Adult patients with heart failure",
        I="Care transitions",
        C="Usual care",
        O=["30-day readmission"],
        inclusion_criteria=["Adults ≥18"],
        exclusion_criteria=["Pediatric"],
    )
    defaults.update(kwargs)
    return PICO(**defaults)


def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-review-f3"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="test-review-f3",
        raw_question="Test question",
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
        authors=["Smith J"],
        year=2022,
        journal="NEJM",
        source_dbs=["pubmed"],
    )
    defaults.update(kwargs)
    return Article(**defaults)


def _write_screened(review_dir: Path, articles: list[Article]) -> None:
    (review_dir / "articles_screened.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2)
    )


def _write_candidates(
    review_dir: Path,
    candidates: list[Article],
    backward_count: int = 0,
    forward_count: int = 0,
) -> None:
    (review_dir / CC_CANDIDATES_FILE).write_text(
        json.dumps({
            "backward_count": backward_count,
            "forward_count": forward_count,
            "candidates": [a.model_dump(mode="json") for a in candidates],
        }, indent=2)
    )


# ---------------------------------------------------------------------------
# Issue 31: borderlines preserved in save()
# ---------------------------------------------------------------------------

class TestCitationChaserSaveBorderlines:
    def test_include_and_borderline_both_appended(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [_make_article(doi="10.1/existing")])
        candidates = [
            _make_article(doi="10.1/a", title="Include article"),
            _make_article(doi="10.1/b", title="Borderline article"),
            _make_article(doi="10.1/c", title="Exclude article"),
        ]
        _write_candidates(review_dir, candidates)

        cc_save(review_dir, json.dumps({"decisions": [
            {"index": 0, "decision": "include", "rationale": "Relevant"},
            {"index": 1, "decision": "borderline", "rationale": "Possibly relevant"},
            {"index": 2, "decision": "exclude", "rationale": "Off-topic"},
        ]}))
        capsys.readouterr()

        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert len(screened) == 3  # original + include + borderline (exclude not appended)

    def test_citation_chasing_added_counts_includes_only(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [])
        candidates = [
            _make_article(doi="10.1/a"),
            _make_article(doi="10.1/b"),
        ]
        _write_candidates(review_dir, candidates)

        cc_save(review_dir, json.dumps({"decisions": [
            {"index": 0, "decision": "include", "rationale": "Relevant"},
            {"index": 1, "decision": "borderline", "rationale": "Unclear"},
        ]}))
        capsys.readouterr()

        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.citation_chasing_added == 1

    def test_citation_chasing_borderlines_counter(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [])
        candidates = [
            _make_article(doi="10.1/a"),
            _make_article(doi="10.1/b"),
            _make_article(doi="10.1/c"),
        ]
        _write_candidates(review_dir, candidates)

        cc_save(review_dir, json.dumps({"decisions": [
            {"index": 0, "decision": "include", "rationale": "Relevant"},
            {"index": 1, "decision": "borderline", "rationale": "Unclear"},
            {"index": 2, "decision": "borderline", "rationale": "Possibly relevant"},
        ]}))
        capsys.readouterr()

        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.citation_chasing_borderlines == 2

    def test_borderline_articles_have_citation_chased_flag(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [])
        _write_candidates(review_dir, [_make_article(doi="10.1/a")])

        cc_save(review_dir, json.dumps({"decisions": [
            {"index": 0, "decision": "borderline", "rationale": "Unclear"},
        ]}))
        capsys.readouterr()

        screened = json.loads((review_dir / "articles_screened.json").read_text())
        assert screened[0]["citation_chased"] is True
        assert screened[0]["screening_decision"] == "borderline"

    def test_save_output_includes_new_borderlines_count(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [])
        candidates = [
            _make_article(doi="10.1/a"),
            _make_article(doi="10.1/b"),
        ]
        _write_candidates(review_dir, candidates)

        cc_save(review_dir, json.dumps({"decisions": [
            {"index": 0, "decision": "include", "rationale": "Relevant"},
            {"index": 1, "decision": "borderline", "rationale": "Unclear"},
        ]}))
        out = json.loads(capsys.readouterr().out)

        assert out["new_borderlines"] == 1
        assert out["new_includes"] == 1


# ---------------------------------------------------------------------------
# Issue 29: _enrich_abstracts() — abstract enrichment for backward candidates
# ---------------------------------------------------------------------------

class TestEnrichAbstracts:
    def test_abstracts_populated_from_s2(self):
        candidates = [
            _make_article(doi="10.1/a", title="Paper A"),
            _make_article(doi="10.1/b", title="Paper B"),
            _make_article(doi="10.1/c", title="Paper C"),
        ]
        mock_s2 = MagicMock()
        mock_s2.batch_lookup = AsyncMock(return_value=[
            _make_article(doi="10.1/a", abstract="Abstract for A"),
            _make_article(doi="10.1/b", abstract="Abstract for B"),
        ])
        asyncio.run(_enrich_abstracts(mock_s2, candidates))

        assert candidates[0].abstract == "Abstract for A"
        assert candidates[1].abstract == "Abstract for B"

    def test_missing_abstract_marked_unavailable(self):
        candidates = [
            _make_article(doi="10.1/a"),
            _make_article(doi="10.1/b"),
            _make_article(doi="10.1/c"),
        ]
        mock_s2 = MagicMock()
        # S2 returns abstract for only 2 of 3
        mock_s2.batch_lookup = AsyncMock(return_value=[
            _make_article(doi="10.1/a", abstract="Abstract A"),
            _make_article(doi="10.1/b", abstract="Abstract B"),
        ])
        asyncio.run(_enrich_abstracts(mock_s2, candidates))

        assert candidates[0].abstract == "Abstract A"
        assert candidates[1].abstract == "Abstract B"
        assert candidates[2].abstract_unavailable is True

    def test_candidate_with_existing_abstract_not_overwritten(self):
        candidate = _make_article(doi="10.1/a", abstract="Existing abstract")
        mock_s2 = MagicMock()
        mock_s2.batch_lookup = AsyncMock(return_value=[
            _make_article(doi="10.1/a", abstract="New abstract from S2"),
        ])
        asyncio.run(_enrich_abstracts(mock_s2, [candidate]))

        assert candidate.abstract == "Existing abstract"
        mock_s2.batch_lookup.assert_not_called()  # no keys built for articles with abstracts

    def test_s2_failure_handled_gracefully(self):
        candidates = [_make_article(doi="10.1/a")]
        mock_s2 = MagicMock()
        mock_s2.batch_lookup = AsyncMock(side_effect=Exception("Rate limited"))
        asyncio.run(_enrich_abstracts(mock_s2, candidates))

        assert candidates[0].abstract is None
        assert candidates[0].abstract_unavailable is False  # no attempt was made


# ---------------------------------------------------------------------------
# Issue 30: _enrich_seeds_s2() — s2_id enrichment for seed articles
# ---------------------------------------------------------------------------

class TestEnrichSeedsS2:
    def test_batch_lookup_called_with_correct_keys(self):
        seeds = [
            _make_article(doi="10.1/a", title="Seed A"),
            _make_article(doi="10.1/b", title="Seed B"),
            _make_article(doi="10.1/c", title="Seed C"),
            _make_article(doi=None, pmid="12345", title="Seed PMID"),
            _make_article(doi=None, pmid=None, title="Seed no ID"),
        ]
        mock_s2 = MagicMock()
        mock_s2.batch_lookup = AsyncMock(return_value=[])
        asyncio.run(_enrich_seeds_s2(mock_s2, seeds))

        call_args = mock_s2.batch_lookup.call_args[0][0]
        assert len(call_args) == 4  # 3 DOI + 1 PMID; 1 with neither excluded
        assert "DOI:10.1/a" in call_args
        assert "DOI:10.1/b" in call_args
        assert "DOI:10.1/c" in call_args
        assert "PMID:12345" in call_args

    def test_s2_ids_populated_on_match(self):
        seeds = [
            _make_article(doi="10.1/a", title="Seed A"),
            _make_article(doi=None, pmid="99999", title="Seed B"),
        ]
        enriched_a = _make_article(doi="10.1/a", s2_id="s2abc123")
        enriched_b = _make_article(doi=None, pmid="99999", s2_id="s2def456")
        mock_s2 = MagicMock()
        mock_s2.batch_lookup = AsyncMock(return_value=[enriched_a, enriched_b])
        asyncio.run(_enrich_seeds_s2(mock_s2, seeds))

        assert seeds[0].s2_id == "s2abc123"
        assert seeds[1].s2_id == "s2def456"

    def test_seed_with_no_ids_left_unchanged(self):
        seeds = [_make_article(doi=None, pmid=None, title="No ID seed")]
        mock_s2 = MagicMock()
        mock_s2.batch_lookup = AsyncMock(return_value=[])
        asyncio.run(_enrich_seeds_s2(mock_s2, seeds))

        assert seeds[0].s2_id is None
        mock_s2.batch_lookup.assert_not_called()  # no keys → no call

    def test_seeds_already_having_s2_id_skipped(self):
        seeds = [
            _make_article(doi="10.1/a", s2_id="existing_id"),
            _make_article(doi="10.1/b"),
        ]
        mock_s2 = MagicMock()
        mock_s2.batch_lookup = AsyncMock(return_value=[])
        asyncio.run(_enrich_seeds_s2(mock_s2, seeds))

        call_args = mock_s2.batch_lookup.call_args[0][0]
        assert len(call_args) == 1  # only the seed missing s2_id
        assert "DOI:10.1/a" not in call_args


# ---------------------------------------------------------------------------
# Issue 30: S2 API key warning in prepare output
# ---------------------------------------------------------------------------

class TestS2ApiKeyWarning:
    def test_no_s2_api_key_emits_retrieval_error(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [
            _make_article(doi="10.1/a", screening_decision=ScreeningDecision.include)
        ])

        with patch("src.skills.citation_chaser.load_api_config") as mock_cfg, \
             patch("src.skills.citation_chaser._enrich_seeds_s2", new=AsyncMock()) as mock_seeds, \
             patch("src.skills.citation_chaser._enrich_abstracts", new=AsyncMock()) as mock_abs, \
             patch("src.skills.citation_chaser._backward_chase", new=AsyncMock(return_value=[])), \
             patch("src.skills.citation_chaser._forward_chase", new=AsyncMock(return_value=[])):
            cfg = MagicMock()
            cfg.semantic_scholar_api_key = None
            cfg.crossref_mailto = "test@example.com"
            mock_cfg.return_value = cfg
            from src.skills.citation_chaser import prepare
            prepare(review_dir)

        out = json.loads(capsys.readouterr().out)
        assert len(out["retrieval_errors"]) == 1
        assert "API key not configured" in out["retrieval_errors"][0]
        assert mock_seeds.called
        assert mock_abs.called

    def test_with_s2_api_key_no_retrieval_error(self, tmp_path, capsys):
        _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
        _write_screened(review_dir, [
            _make_article(doi="10.1/a", screening_decision=ScreeningDecision.include)
        ])

        with patch("src.skills.citation_chaser.load_api_config") as mock_cfg, \
             patch("src.skills.citation_chaser._enrich_seeds_s2", new=AsyncMock()), \
             patch("src.skills.citation_chaser._enrich_abstracts", new=AsyncMock()), \
             patch("src.skills.citation_chaser._backward_chase", new=AsyncMock(return_value=[])), \
             patch("src.skills.citation_chaser._forward_chase", new=AsyncMock(return_value=[])):
            cfg = MagicMock()
            cfg.semantic_scholar_api_key = "my-s2-key"
            cfg.crossref_mailto = "test@example.com"
            mock_cfg.return_value = cfg
            from src.skills.citation_chaser import prepare
            prepare(review_dir)

        out = json.loads(capsys.readouterr().out)
        assert out["retrieval_errors"] == []


# ---------------------------------------------------------------------------
# Perf: _forward_chase parallelization
# ---------------------------------------------------------------------------

class TestForwardChaseParallelization:
    def test_get_citations_called_for_all_seeds_with_s2_id(self):
        seeds = [
            _make_article(doi="10.1/a", s2_id="s2_001"),
            _make_article(doi="10.1/b", s2_id="s2_002"),
            _make_article(doi="10.1/c", s2_id="s2_003"),
            _make_article(doi="10.1/d"),  # no s2_id — should be skipped
        ]
        mock_s2 = MagicMock()
        mock_s2.get_citations = AsyncMock(return_value=[])
        mock_s2.batch_lookup = AsyncMock(return_value=[])

        asyncio.run(_forward_chase(mock_s2, seeds, set()))

        assert mock_s2.get_citations.call_count == 3  # 3 seeds with s2_id
        called_ids = {call.args[0] for call in mock_s2.get_citations.call_args_list}
        assert called_ids == {"s2_001", "s2_002", "s2_003"}

    def test_seed_without_s2_id_not_queried(self):
        seeds = [_make_article(doi="10.1/a")]  # no s2_id
        mock_s2 = MagicMock()
        mock_s2.get_citations = AsyncMock(return_value=[])
        mock_s2.batch_lookup = AsyncMock(return_value=[])

        asyncio.run(_forward_chase(mock_s2, seeds, set()))

        mock_s2.get_citations.assert_not_called()
