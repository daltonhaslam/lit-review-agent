"""Stage 9.6 --import-from PATH walks PATH for *.pdf files,
matches each to a retrieval_list.csv row, renames to the canonical
{PMID}.pdf or {DOI_safe}.pdf, and MOVES to pdfs/. Unmatched files stay in
PATH. Magic-bytes-failed files are skipped (don't move HTML stubs)."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.skills.pdf_downloader import import_from


def _setup(tmp_path, csv_rows: list[dict]):
    rd = tmp_path / "rv"; rd.mkdir(); (rd / "pdfs").mkdir()
    downloads = tmp_path / "downloads"; downloads.mkdir()
    csv_path = rd / "retrieval_list.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pmid", "doi", "pdf_status", "pdf_path"])
        w.writeheader(); w.writerows(csv_rows)
    return rd, downloads


def test_imports_bare_pmid_pdf(tmp_path):
    rd, dl = _setup(tmp_path, [{"pmid": "38713841", "doi": "", "pdf_status": "not_found", "pdf_path": ""}])
    src = dl / "38713841.pdf"
    src.write_bytes(b"%PDF-1.5\nreal pdf")
    result = import_from(rd, dl)
    assert result["imported_count"] == 1
    assert not src.exists()
    assert (rd / "pdfs" / "38713841.pdf").exists()


def test_imports_doi_named_pdf(tmp_path):
    rd, dl = _setup(tmp_path, [{"pmid": "", "doi": "10.1377/hlthaff.2019.01019", "pdf_status": "not_found", "pdf_path": ""}])
    src = dl / "10.1377_hlthaff.2019.01019.pdf"
    src.write_bytes(b"%PDF-1.5\nreal pdf")
    result = import_from(rd, dl)
    assert result["imported_count"] == 1
    assert (rd / "pdfs" / "10.1377_hlthaff.2019.01019.pdf").exists()


def test_leaves_unmatched_files_alone(tmp_path):
    rd, dl = _setup(tmp_path, [{"pmid": "1", "doi": "", "pdf_status": "not_found", "pdf_path": ""}])
    src = dl / "random_paper_99999.pdf"
    src.write_bytes(b"%PDF-1.5\nreal pdf")
    result = import_from(rd, dl)
    assert result["imported_count"] == 0
    assert any("random_paper_99999.pdf" in u["filename"] for u in result["unmatched"])
    assert src.exists()  # NOT moved


def test_rejects_non_pdf_files(tmp_path):
    rd, dl = _setup(tmp_path, [{"pmid": "1", "doi": "", "pdf_status": "not_found", "pdf_path": ""}])
    src = dl / "1.pdf"
    src.write_bytes(b"<!doctype html>not a pdf")
    result = import_from(rd, dl)
    assert result["imported_count"] == 0
    assert any(u["reason"] == "non-pdf-content" for u in result["unmatched"])
    assert src.exists()


def test_runs_reconcile_after_import(tmp_path):
    rd, dl = _setup(tmp_path, [{"pmid": "1", "doi": "", "pdf_status": "not_found", "pdf_path": ""}])
    (dl / "1.pdf").write_bytes(b"%PDF-1.5\nreal pdf")
    import_from(rd, dl)
    rows = list(csv.DictReader((rd / "retrieval_list.csv").open()))
    assert rows[0]["pdf_status"] == "downloaded"
