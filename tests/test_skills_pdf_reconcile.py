"""Issue #47 / #44: filename → retrieval row matcher. Pattern cascade:
1. Bare PMID (`{digits}.pdf`)
2. DOI in filename (canonical `_normalize_doi_for_filename` form)
3. Elsevier PII (`1-s2.0-{PII}-main.pdf`) — defers to caller for PMID lookup
"""
from __future__ import annotations

import pytest

from src.skills.pdf_downloader import _match_filename_to_article


def _row(pmid="", doi=""): return {"pmid": pmid, "doi": doi}


def test_bare_pmid_filename_matches():
    rows = [_row(pmid="38713841"), _row(pmid="38713842")]
    matched, method = _match_filename_to_article("38713841.pdf", rows)
    assert matched is rows[0]
    assert method == "pmid_in_filename"


def test_doi_in_filename_matches():
    rows = [_row(doi="10.1377/hlthaff.2019.01019")]
    matched, method = _match_filename_to_article("10.1377_hlthaff.2019.01019.pdf", rows)
    assert matched is rows[0]
    assert method == "doi_in_filename"


def test_doi_in_filename_matches_case_insensitively():
    # SAGE/Wiley DOIs contain uppercase letters (e.g. 1357633X). The filename preserves the
    # original case, but _normalize_doi_for_filename lowercases — so the compare must lowercase
    # the stem too, or the PDF is stranded despite being on disk.
    rows = [_row(doi="10.1177/1357633X19845835")]
    matched, method = _match_filename_to_article("10.1177_1357633X19845835.pdf", rows)
    assert matched is rows[0]
    assert method == "doi_in_filename"


def test_no_match_returns_none_with_reason():
    rows = [_row(pmid="111")]
    matched, reason = _match_filename_to_article("unknown_file.pdf", rows)
    assert matched is None
    assert "no" in reason.lower()  # "no pattern matched" or "no row"


def test_extension_required():
    rows = [_row(pmid="123")]
    matched, reason = _match_filename_to_article("123", rows)
    assert matched is None  # not a .pdf


import csv
import json
from pathlib import Path
from src.skills.pdf_downloader import reconcile_disk


def _setup(tmp_path, pdf_files, csv_rows):
    rd = tmp_path / "rv"; rd.mkdir(); (rd / "pdfs").mkdir()
    for filename in pdf_files:
        (rd / "pdfs" / filename).write_bytes(b"%PDF-1.5\n")
    csv_path = rd / "retrieval_list.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pmid", "doi", "pdf_status", "pdf_path"])
        w.writeheader(); w.writerows(csv_rows)
    return rd


def test_reconcile_flips_not_found_to_downloaded(tmp_path):
    rd = _setup(tmp_path,
        pdf_files=["38713841.pdf"],
        csv_rows=[{"pmid": "38713841", "doi": "", "pdf_status": "not_found", "pdf_path": ""}])
    result = reconcile_disk(rd)
    assert result["reconciled"] == 1
    rows = list(csv.DictReader((rd / "retrieval_list.csv").open()))
    assert rows[0]["pdf_status"] == "downloaded"
    assert rows[0]["pdf_path"] == "pdfs/38713841.pdf"


def test_reconcile_matches_uppercase_pdf_extension(tmp_path):
    # A file named `38713841.PDF` (uppercase extension) must still be reconciled — case-sensitive
    # glob('*.pdf') would silently skip it, leaving the article marked not_found on disk.
    rd = _setup(tmp_path,
        pdf_files=["38713841.PDF"],
        csv_rows=[{"pmid": "38713841", "doi": "", "pdf_status": "not_found", "pdf_path": ""}])
    result = reconcile_disk(rd)
    assert result["reconciled"] == 1
    rows = list(csv.DictReader((rd / "retrieval_list.csv").open()))
    assert rows[0]["pdf_status"] == "downloaded"


def test_reconcile_idempotent_for_already_downloaded(tmp_path):
    rd = _setup(tmp_path,
        pdf_files=["1.pdf"],
        csv_rows=[{"pmid": "1", "doi": "", "pdf_status": "downloaded", "pdf_path": "pdfs/1.pdf"}])
    result = reconcile_disk(rd)
    assert result["reconciled"] == 0
    assert result["already_correct"] == 1


def test_reconcile_reports_orphan_files(tmp_path):
    """A PDF on disk with no matching CSV row is reported but not deleted."""
    rd = _setup(tmp_path,
        pdf_files=["orphan_99999.pdf"],
        csv_rows=[{"pmid": "1", "doi": "", "pdf_status": "downloaded", "pdf_path": "pdfs/1.pdf"}])
    result = reconcile_disk(rd)
    assert any("orphan_99999.pdf" in n for n in result["no_match_in_csv"])
    assert (rd / "pdfs" / "orphan_99999.pdf").exists()  # not deleted


def test_fulltext_assessor_prepare_runs_reconcile(tmp_path, capsys):
    """Stage 10 prepare must auto-reconcile so a PDF dropped in pdfs/ outside
    the pipeline (manual library fetch, Stage 9.6 import) becomes visible."""
    import json
    from src.agent import state as state_manager
    from src.models.article import Article, ScreeningDecision
    from src.models.pico import PICO
    from src.models.review import Review, ReviewStatus, ReviewType
    from src.skills.fulltext_assessor import prepare

    rd = tmp_path / "rv"; rd.mkdir(); (rd / "pdfs").mkdir()
    review = Review(
        review_id="rv", raw_question="q",
        review_type=ReviewType.systematic,
        status=ReviewStatus.retrieval_list_generated,
        pico=PICO(P="x", I="y", C="z", O=["o"], inclusion_criteria=["a"], exclusion_criteria=["b"]),
        articles_screened_file="articles_screened.json",
    )
    state_manager.save(review, rd)
    arts = [Article(pmid="1", title="t", screening_decision=ScreeningDecision.include).model_dump(mode="json")]
    (rd / "articles_screened.json").write_text(json.dumps(arts))
    # PDF dropped on disk; retrieval_list.csv says not_found
    (rd / "pdfs" / "1.pdf").write_bytes(b"%PDF-1.5\n")
    import csv
    with (rd / "retrieval_list.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pmid", "doi", "pdf_status", "pdf_path"])
        w.writeheader()
        w.writerow({"pmid": "1", "doi": "", "pdf_status": "not_found", "pdf_path": ""})

    prepare(rd)
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    # reconcile_summary should be emitted in the prepare output
    assert "reconcile_summary" in out
    assert out["reconcile_summary"]["reconciled"] >= 1
