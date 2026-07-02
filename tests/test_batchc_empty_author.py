"""Regression tests: empty/whitespace first-author strings must not crash.

A hand-edited or library-imported record can carry ``authors=[""]`` (or
``["   "]``). The list is non-empty, so a bare truthiness guard passes, but
``"".split()`` is ``[]`` — so ``authors[0].split()[0]`` raises IndexError.
These tests pin all three affected sites to their existing sentinels instead.
"""

from src.models.article import Article, EvidenceLevel, ScreeningDecision
from src.skills.synthesizer import _author_year
import src.skills.packager as packager


def _make_article(**kwargs) -> Article:
    defaults = dict(
        title="A study on heart failure readmission",
        authors=["Smith J", "Jones A", "Brown K"],
        year=2022,
        journal="NEJM",
        source_dbs=["pubmed"],
        study_design="RCT",
        evidence_level=EvidenceLevel.level_II,
        quality_score=0.82,
        quality_instrument="RoB 2.0",
        sample_size=350,
        primary_outcomes=["30-day all-cause readmission"],
        effect_sizes="OR 0.74 (95% CI 0.62–0.88), p=0.001",
        author_conclusions="Care transitions interventions reduced readmission.",
        screening_decision=ScreeningDecision.include,
    )
    defaults.update(kwargs)
    return Article(**defaults)


# --- synthesizer._author_year ------------------------------------------------

def test_author_year_empty_first_author_falls_back_to_unknown():
    a = _make_article(authors=[""], year=2022)
    assert _author_year(a) == "Unknown 2022"


def test_author_year_whitespace_first_author_falls_back_to_unknown():
    a = _make_article(authors=["   "], year=2021)
    assert _author_year(a) == "Unknown 2021"


# --- packager._reference_sort_key --------------------------------------------

def test_reference_sort_key_empty_first_author_falls_back_to_zzz():
    key = packager._reference_sort_key({"authors": [""], "year": 2022})
    assert key == ("zzz", 2022)


def test_reference_sort_key_whitespace_first_author_falls_back_to_zzz():
    key = packager._reference_sort_key({"authors": ["   "], "year": 2020})
    assert key == ("zzz", 2020)


# --- packager._citation_token ------------------------------------------------

def test_citation_token_empty_first_author_returns_none():
    token = packager._citation_token({"authors": [""], "year": 2023})
    assert token is None


def test_citation_token_whitespace_first_author_returns_none():
    token = packager._citation_token({"authors": ["   "], "year": 2023})
    assert token is None
