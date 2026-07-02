"""Issue #45: fulltext_assessor must support --slice-of/--slice-index for
parallel sub-agent fan-out. Partition the eligible PDF-matched article list
deterministically by sorted orig_idx and emit only the slice plus shared
pico_context/inclusion_criteria/exclusion_criteria/assessment_task."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent import state as state_manager
from src.models.article import Article, ScreeningDecision
from src.models.pico import PICO
from src.models.review import Review, ReviewStatus, ReviewType
from unittest.mock import patch

from src.skills.fulltext_assessor import prepare, _title_coverage_in_pdf, _pdf_match_confidence


def _make_review(tmp_path: Path, n_articles: int = 10) -> Path:
    rd = tmp_path / "rv"
    rd.mkdir()
    (rd / "pdfs").mkdir()
    review = Review(
        review_id="rv",
        raw_question="q",
        review_type=ReviewType.systematic,
        status=ReviewStatus.retrieval_list_generated,
        pico=PICO(P="x", I="y", C="z", O=["o"], inclusion_criteria=["a"], exclusion_criteria=["b"]),
        articles_screened_file="articles_screened.json",
    )
    state_manager.save(review, rd)
    arts = []
    for i in range(n_articles):
        a = Article(pmid=str(i), title=f"Article {i}", screening_decision=ScreeningDecision.include)
        arts.append(a.model_dump(mode="json"))
    (rd / "articles_screened.json").write_text(json.dumps(arts, indent=2))
    return rd


def test_prepare_with_slice_emits_only_that_slice(tmp_path, capsys):
    rd = _make_review(tmp_path, n_articles=10)
    prepare(rd, slice_of=2, slice_index=0)
    out = json.loads(capsys.readouterr().out)
    assert out["total_eligible"] == 10
    # Slice 0 of 2 → first 5 articles
    assert len(out["articles"]) == 5
    orig_ids = [a["orig_idx"] for a in out["articles"]]
    assert orig_ids == sorted(orig_ids), "slice must be in deterministic sorted order"
    assert orig_ids == [0, 1, 2, 3, 4]


def test_prepare_with_second_slice_returns_remaining(tmp_path, capsys):
    rd = _make_review(tmp_path, n_articles=10)
    prepare(rd, slice_of=2, slice_index=1)
    out = json.loads(capsys.readouterr().out)
    assert len(out["articles"]) == 5
    assert [a["orig_idx"] for a in out["articles"]] == [5, 6, 7, 8, 9]


def test_prepare_without_slice_emits_all_articles(tmp_path, capsys):
    rd = _make_review(tmp_path, n_articles=10)
    prepare(rd)
    out = json.loads(capsys.readouterr().out)
    assert len(out["articles"]) == 10


def test_invalid_slice_spec_dies(tmp_path):
    rd = _make_review(tmp_path, n_articles=10)
    with pytest.raises(SystemExit):
        prepare(rd, slice_of=2, slice_index=5)  # index out of range


def test_single_slice_returns_all(tmp_path, capsys):
    """slice_of=1, slice_index=0 must return the full eligible set."""
    rd = _make_review(tmp_path, n_articles=10)
    prepare(rd, slice_of=1, slice_index=0)
    out = json.loads(capsys.readouterr().out)
    assert len(out["articles"]) == 10
    assert out["total_eligible"] == 10


def test_title_coverage_high_for_matching_text():
    title = "Pediatric subspecialty telemedicine consult outcomes"
    text = "This study evaluates pediatric subspecialty telemedicine consult outcomes in 12 community hospitals over 3 years..."
    coverage = _title_coverage_in_pdf(title, text)
    # All significant title words present → full coverage.
    assert coverage == 1.0


def test_title_coverage_low_for_mismatched_text():
    title = "Pediatric subspecialty telemedicine consult outcomes"
    text = "Coeliac disease screening is recommended for first-degree relatives of patients with confirmed disease..."
    coverage = _title_coverage_in_pdf(title, text)
    assert coverage < 0.50


def test_title_coverage_handles_empty_inputs():
    assert _title_coverage_in_pdf("", "anything") == 0.0
    assert _title_coverage_in_pdf("anything else", "") == 0.0


def test_pdf_match_confidence_flags_short_text():
    # < 500 chars → low confidence regardless of coverage
    title = "Pediatric subspecialty telemedicine"
    text = "Pediatric subspecialty telemedicine — short stub"
    low_conf, reason = _pdf_match_confidence(title, text)
    assert low_conf is True
    assert "length" in reason.lower()


def test_pdf_match_confidence_flags_low_coverage():
    title = "Pediatric subspecialty telemedicine consult outcomes in community hospitals"
    text = "Coeliac disease " * 100  # 1500+ chars but completely off-topic
    low_conf, reason = _pdf_match_confidence(title, text)
    assert low_conf is True
    assert "coverage" in reason.lower()


def test_pdf_match_confidence_passes_good_match():
    title = "Pediatric subspecialty telemedicine consult outcomes in community hospitals"
    text = ("This pediatric subspecialty telemedicine consult study evaluates outcomes "
            "across 12 community hospitals using a retrospective cohort design. The "
            "intervention consisted of subspecialty consults via telemedicine for "
            "pediatric inpatients. ") * 5
    low_conf, reason = _pdf_match_confidence(title, text)
    assert low_conf is False
    assert reason == ""


def test_prepare_emits_pdf_match_warnings(tmp_path, capsys):
    """Wire-through: when extracted text is too short, prepare's output must
    include a pdf_match_warnings entry and per-article pdf_low_confidence flag.

    We patch _extract_text to return a short stub string so the test is
    independent of pypdf's handling of invalid PDF bytes.
    """
    rd = _make_review(tmp_path, n_articles=2)
    # Create a real file so _match_pdfs can find it by PMID stem "0"
    (rd / "pdfs" / "0.pdf").write_bytes(b"%PDF-1.4 stub")

    SHORT_TEXT = "short stub"  # well under 500 chars

    with patch("src.skills.fulltext_assessor._extract_text_and_full",
               return_value=(SHORT_TEXT, SHORT_TEXT)):
        prepare(rd)

    out = json.loads(capsys.readouterr().out)
    matched = [a for a in out["articles"] if a["pdf_found"]]
    assert matched, "article 0 should be matched to 0.pdf"
    flagged = [a for a in matched if a.get("pdf_low_confidence")]
    assert flagged, "short-text stub should be flagged low_confidence"
    assert out["low_confidence_count"] >= 1
    assert len(out["pdf_match_warnings"]) >= 1
