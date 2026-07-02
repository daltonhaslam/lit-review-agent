"""Unit tests for Pydantic models (Phase 0)."""
import pytest
from datetime import datetime

from src.models.article import Article, ScreeningDecision, EvidenceLevel
from src.models.pico import PICO, FundamentalFour
from src.models.query import Query, QuerySet
from src.models.review import Review, ReviewType, ReviewStatus, ScreeningMode, PRISMACounts


# --- Article ---

def test_article_minimal():
    a = Article(title="Test Article")
    assert a.title == "Test Article"
    assert a.pmid is None
    assert a.screening_decision == ScreeningDecision.pending
    assert a.source_dbs == []


def test_article_full_roundtrip():
    a = Article(
        pmid="12345678",
        doi="10.1001/test.2024",
        title="Heart Failure Readmission Study",
        abstract="Background: ...",
        authors=["Smith J", "Jones A"],
        year=2024,
        journal="JAMA",
        source_dbs=["pubmed", "openalex"],
        mesh_terms=["Heart Failure", "Patient Readmission"],
        citation_count=42,
        screening_decision=ScreeningDecision.include,
        evidence_level=EvidenceLevel.level_II,
    )
    data = a.model_dump()
    a2 = Article.model_validate(data)
    assert a2.pmid == "12345678"
    assert a2.screening_decision == ScreeningDecision.include
    assert a2.evidence_level == EvidenceLevel.level_II
    assert a2.source_dbs == ["pubmed", "openalex"]


def test_screening_decision_values():
    assert set(ScreeningDecision) == {
        ScreeningDecision.include,
        ScreeningDecision.exclude,
        ScreeningDecision.borderline,
        ScreeningDecision.pending,
    }


def test_evidence_level_values():
    levels = {e.value for e in EvidenceLevel}
    assert "I" in levels
    assert "III-2" in levels
    assert "IV" in levels


# --- PICO ---

def test_pico_minimal():
    p = PICO(P="Adults with CHF", I="Care transitions", C="Usual care", O=["30-day readmission"])
    assert p.P == "Adults with CHF"
    assert p.O == ["30-day readmission"]
    assert p.S is None
    assert p.inclusion_criteria == []


def test_pico_with_fundamental_four():
    ff = FundamentalFour(
        what_we_know="CHF is a leading cause of readmission",
        what_we_dont_know="Which interventions work best",
        what_we_should_know="Effect size of transition programs",
        why_we_should_know="Guides quality improvement investment",
    )
    p = PICO(P="CHF patients", I="Transition program", C="Standard care", O=["Readmission"], fundamental_four=ff)
    assert p.fundamental_four.what_we_know == "CHF is a leading cause of readmission"


def test_pico_roundtrip():
    p = PICO(
        P="Adults", I="Intervention", C="Control", O=["Outcome 1", "Outcome 2"],
        inclusion_criteria=["RCT only", "Adults 18+"],
        exclusion_criteria=["Pediatric"],
        raw_question="Does X reduce Y?",
    )
    p2 = PICO.model_validate(p.model_dump())
    assert p2.inclusion_criteria == ["RCT only", "Adults 18+"]
    assert len(p2.O) == 2


# --- Query ---

def test_query_defaults():
    q = Query(step_number="#1", query_string='"Heart Failure"[Mesh]', database="pubmed")
    assert q.hits is None
    assert isinstance(q.timestamp, datetime)


def test_queryset_empty():
    qs = QuerySet()
    assert qs.pubmed == []
    assert qs.openalex == []
    assert qs.semantic_scholar == []


def test_queryset_with_queries():
    q1 = Query(step_number="#1", query_string='"Heart Failure"[Mesh]', database="pubmed", hits=245000)
    q2 = Query(step_number="#2", query_string='heart failure*[tiab]', database="pubmed", hits=198000)
    qs = QuerySet(pubmed=[q1, q2])
    assert len(qs.pubmed) == 2
    assert qs.pubmed[1].hits == 198000


# --- Review ---

def test_review_minimal():
    r = Review(review_id="test-review-2026-04")
    assert r.status == ReviewStatus.new
    assert r.screening_mode == ScreeningMode.thorough
    assert r.prisma_counts.identified == 0
    assert r.iterations == 0


def test_review_status_progression():
    r = Review(review_id="test-review-2026-04")
    r.status = ReviewStatus.pico_formulated
    assert r.status == ReviewStatus.pico_formulated


def test_prisma_counts():
    pc = PRISMACounts(identified=500, screened=480, eligibility=40, included=25)
    assert pc.identified >= pc.screened >= pc.eligibility >= pc.included


def test_review_roundtrip():
    r = Review(
        review_id="chf-readmission-2026-04",
        review_type=ReviewType.systematic,
        raw_question="Does early mobility reduce LOS in ICU patients?",
        status=ReviewStatus.deduplicated,
    )
    r.prisma_counts.identified = 312
    data = r.model_dump_json()
    r2 = Review.model_validate_json(data)
    assert r2.review_id == "chf-readmission-2026-04"
    assert r2.review_type == ReviewType.systematic
    assert r2.prisma_counts.identified == 312
