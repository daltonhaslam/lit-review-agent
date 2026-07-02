"""Batch C regression test — deduplicator: DOI acquired via merge must be
registered in seen_dois so a later article carrying the same DOI is caught.

Scenario (surviving-duplicate bug):
  A — no DOI, title T1, author Smith J / 2020
  B — DOI 10.1/x, title T1 with a tiny variation → title-sim ≥ 0.92 merges into A,
      giving A the DOI via _merge_sources
  C — DOI 10.1/x, clearly different title (title-sim < 0.92) and no matching
      author/year → falls through every tier

Before the fix, A's acquired DOI was never inserted into seen_dois, so C was
appended as a separate record (3 articles). It should be caught as a DOI
duplicate (2 articles).
"""
from __future__ import annotations

import json
from pathlib import Path

from src.agent import state as state_manager
from src.models.article import Article
from src.models.pico import PICO
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewStatus, ReviewType, ScreeningMode
from src.skills.deduplicator import deduplicate


def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-chf-2026-04"
    review_dir.mkdir()
    pico = PICO(
        P="Heart failure patients",
        I="Care transitions interventions",
        C="Usual care",
        O=["30-day readmission rate"],
    )
    queries = QuerySet(
        pubmed=[
            Query(step_number="#1", query_string='"Heart Failure"[Mesh]', database="pubmed"),
        ],
    )
    review = Review(
        review_id="test-chf-2026-04",
        raw_question="Does care transitions reduce CHF readmission?",
        review_type=ReviewType.systematic,
        screening_mode=ScreeningMode.thorough,
        pico=pico,
        queries=queries,
        status=ReviewStatus.queries_built,
        **kwargs,
    )
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


def _write_raw(review_dir: Path, articles: list[Article]) -> None:
    path = review_dir / "articles_raw.json"
    path.write_text(json.dumps([a.model_dump(mode="json") for a in articles], indent=2))


class TestMergedDoiRegistered:
    def test_doi_acquired_via_merge_catches_later_duplicate(self, tmp_path):
        _, review_dir = _make_review(tmp_path)
        articles = [
            # A: no DOI; will absorb B and acquire B's DOI
            _make_article(
                title="Transitions of care reduce readmission in heart failure patients",
                authors=["Smith J"],
                year=2020,
                doi=None,
                source_dbs=["pubmed"],
            ),
            # B: tiny title variation → title-similarity merges into A, giving A the DOI
            _make_article(
                title="Transitions of care reduces readmission in heart failure patients",
                authors=["Smith J"],
                year=2020,
                doi="10.1/x",
                source_dbs=["openalex"],
            ),
            # C: same DOI as B, but a clearly different title (sim < 0.92) and a
            # non-matching author/year so the author-year fallback misses.
            _make_article(
                title="ICU early mobility protocols reduce hospital length of stay",
                authors=["Nguyen T"],
                year=2015,
                doi="10.1/x",
                source_dbs=["europepmc"],
            ),
        ]
        _write_raw(review_dir, articles)
        review = state_manager.load(review_dir)
        review.status = ReviewStatus.aggregated
        state_manager.save(review, review_dir)

        result = deduplicate(review_dir)

        # A and B merge into one record (acquiring DOI 10.1/x); C shares that DOI
        # and must be caught as a DOI duplicate → 1 surviving record, not 2.
        assert result["after"] == 1, (
            f"C carrying the acquired DOI should be caught, expected 1 got {result['after']}"
        )
        assert result["by_method"]["doi_match"] == 1

        saved = state_manager.load(review_dir)
        assert saved.prisma_counts.identified == 1

        # Invariant: no two surviving records may share a normalized DOI.
        deduped_path = review_dir / "articles_deduped.json"
        deduped = [Article(**a) for a in json.loads(deduped_path.read_text())]
        dois = [a.doi for a in deduped if a.doi]
        assert len(dois) == len(set(dois)), f"surviving duplicate DOI(s): {dois}"
