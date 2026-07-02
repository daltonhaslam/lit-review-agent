"""Issue #46: magic-bytes validation. is_pdf_bytes() must reject HTML
masquerading as PDF; --validate-disk walks pdfs/ and quarantines any non-PDF
files in pdfs/_rejected/."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.skills.pdf_downloader import is_pdf_bytes, validate_disk


def test_is_pdf_bytes_accepts_pdf_magic():
    assert is_pdf_bytes(b"%PDF-1.5\nrest of file") is True


def test_is_pdf_bytes_rejects_html():
    assert is_pdf_bytes(b"<!DOCTYPE html><html>not a pdf</html>") is False


def test_is_pdf_bytes_rejects_empty():
    assert is_pdf_bytes(b"") is False


def test_is_pdf_bytes_rejects_short():
    assert is_pdf_bytes(b"%PD") is False  # too short to confirm magic


def test_validate_disk_quarantines_non_pdf(tmp_path):
    """A pdfs/ dir containing one real PDF and one HTML stub: --validate-disk
    moves the HTML stub to pdfs/_rejected/ and leaves the real PDF alone."""
    review_dir = tmp_path / "rv"
    review_dir.mkdir()
    pdfs_dir = review_dir / "pdfs"
    pdfs_dir.mkdir()
    (pdfs_dir / "good.pdf").write_bytes(b"%PDF-1.5\nreal pdf content")
    (pdfs_dir / "bad.pdf").write_bytes(b"<!doctype html>not a pdf")
    # Retrieval CSV so the function can update pdf_status entries
    retrieval = review_dir / "retrieval_list.csv"
    with retrieval.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pmid", "doi", "pdf_status", "pdf_path"])
        w.writeheader()
        w.writerow({"pmid": "good", "doi": "", "pdf_status": "downloaded", "pdf_path": "pdfs/good.pdf"})
        w.writerow({"pmid": "bad",  "doi": "", "pdf_status": "downloaded", "pdf_path": "pdfs/bad.pdf"})

    result = validate_disk(review_dir)

    assert (pdfs_dir / "good.pdf").exists()
    assert not (pdfs_dir / "bad.pdf").exists()
    assert (pdfs_dir / "_rejected" / "bad.pdf").exists()
    assert result["validated"] == 2
    assert any(r["filename"] == "bad.pdf" for r in result["rejected"])

    with retrieval.open() as f:
        rows = list(csv.DictReader(f))
    bad_row = next(r for r in rows if r["pmid"] == "bad")
    assert bad_row["pdf_status"] == "failed"
    assert bad_row["pdf_path"] == ""
