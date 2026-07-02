"""Issue #50: synthesizer._data_completeness must count articles missing
authors or year and raise data_completeness_warning so the user is routed
to re-run enrichment (Stage 12) instead of producing a synthesis with
fabricated or missing inline citations."""
from __future__ import annotations

from src.models.article import Article, ScreeningDecision
from src.skills.synthesizer import _data_completeness


def _a(**kw):
    base = dict(pmid="x", title="t", screening_decision=ScreeningDecision.include)
    base.update(kw)
    return Article(**base)


def test_warning_fires_when_authors_missing():
    arts = [
        _a(pmid="1", authors=["Smith J"], year=2020),
        _a(pmid="2", authors=[], year=2021),  # missing authors
    ]
    stats, warning = _data_completeness(arts)
    assert stats["total_missing_authors"] == 1
    assert stats["total_missing_year"] == 0
    assert warning is True


def test_warning_fires_when_year_missing():
    arts = [
        _a(pmid="1", authors=["Smith J"], year=2020),
        _a(pmid="2", authors=["Lee K"], year=None),  # missing year
    ]
    stats, warning = _data_completeness(arts)
    assert stats["total_missing_year"] == 1
    assert warning is True


def test_no_warning_when_all_complete():
    arts = [_a(authors=["Smith J"], year=2020, study_design="rct", quality_score=0.7)]
    stats, warning = _data_completeness(arts)
    assert stats["total_missing_authors"] == 0
    assert stats["total_missing_year"] == 0
    assert warning is False


def test_pct_missing_authors_computed():
    arts = [
        _a(pmid="1", authors=["A"], year=2020),
        _a(pmid="2", authors=[], year=2020),
        _a(pmid="3", authors=[], year=2020),
        _a(pmid="4", authors=["B"], year=2020),
    ]
    stats, _ = _data_completeness(arts)
    assert stats["pct_missing_authors"] == 0.5
