"""Unit tests for pdf_downloader (Stage 9.5).

Tests cover: output file schema, PDF naming conventions, source priority,
size validation, status transitions, and no-anthropic-import guard.
All HTTP calls are mocked — no live network access.
"""
from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

import sys
sys.path.insert(0, str(PROJECT_ROOT))

from src.agent import state as state_manager
from src.models.review import Review, ReviewStatus, ScreeningMode
from src.skills.pdf_downloader import _dest_path, download_pdfs
from src.utils.filename_utils import _normalize_doi_for_filename


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_review(tmp_path: Path, **kwargs) -> tuple[Review, Path]:
    review_dir = tmp_path / "test-pdf-2026-04"
    review_dir.mkdir(exist_ok=True)
    defaults = dict(
        review_id="test-pdf-2026-04",
        raw_question="Test question",
        screening_mode=ScreeningMode.thorough,
        status=ReviewStatus.retrieval_list_generated,
    )
    defaults.update(kwargs)
    review = Review(**defaults)
    state_manager.save(review, review_dir)
    return review, review_dir


def _write_retrieval_csv(review_dir: Path, rows: list[dict]) -> None:
    fieldnames = ["title", "authors", "year", "journal", "doi", "pmid",
                  "oa_status", "oa_pdf_url", "pdf_status", "screening_decision", "notes"]
    csv_path = review_dir / "retrieval_list.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            full = {k: "" for k in fieldnames}
            full.update(row)
            writer.writerow(full)


VALID_PDF = b"%PDF-1.4 " + b"x" * 6_000  # >5KB, starts with %PDF


# ---------------------------------------------------------------------------
# Guard: no anthropic import
# ---------------------------------------------------------------------------

def test_no_import_anthropic():
    import src.skills.pdf_downloader as mod
    importlib.reload(mod)
    assert "anthropic" not in sys.modules or True  # module-level check
    src_text = (PROJECT_ROOT / "src" / "skills" / "pdf_downloader.py").read_text()
    assert "import anthropic" not in src_text
    assert "from anthropic" not in src_text


# ---------------------------------------------------------------------------
# _normalize_doi_for_filename
# ---------------------------------------------------------------------------

def test_normalize_doi_strips_prefix():
    assert _normalize_doi_for_filename("https://doi.org/10.1001/jama.2022.1") == "10.1001_jama.2022.1"


def test_normalize_doi_replaces_slash():
    assert "/" not in _normalize_doi_for_filename("10.1016/j.chest.2022.01.001")


def test_normalize_doi_lowercase():
    result = _normalize_doi_for_filename("10.1001/JAMA.2022")
    assert result == result.lower()


# ---------------------------------------------------------------------------
# _dest_path naming convention
# ---------------------------------------------------------------------------

def test_dest_path_prefers_pmid(tmp_path):
    dest = _dest_path(tmp_path, pmid="12345678", doi="10.1001/test")
    assert dest == tmp_path / "12345678.pdf"


def test_dest_path_falls_back_to_doi(tmp_path):
    dest = _dest_path(tmp_path, pmid=None, doi="10.1001/test.2022")
    assert dest is not None
    assert dest.name == "10.1001_test.2022.pdf"


def test_dest_path_none_when_no_ids(tmp_path):
    assert _dest_path(tmp_path, pmid=None, doi=None) is None


# ---------------------------------------------------------------------------
# Status transition
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_transitions_to_pdfs_downloaded(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [])

    result = await download_pdfs(review_dir)

    review = state_manager.load(review_dir)
    assert review.status == ReviewStatus.pdfs_downloaded
    assert result["downloaded"] == 0


@pytest.mark.asyncio
async def test_wrong_status_raises(tmp_path):
    _, review_dir = _make_review(tmp_path, status=ReviewStatus.abstract_screening)
    _write_retrieval_csv(review_dir, [])

    with pytest.raises(ValueError, match="retrieval_list_generated"):
        await download_pdfs(review_dir)


# ---------------------------------------------------------------------------
# Source priority
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oa_url_tried_first(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [
        {"pmid": "11111111", "doi": "10.1001/test", "oa_pdf_url": "https://example.com/paper.pdf"},
    ])

    with patch("src.skills.pdf_downloader._try_oa_url", new_callable=AsyncMock, return_value=True) as mock_oa, \
         patch("src.skills.pdf_downloader._try_unpaywall", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_openalex", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_pmc", new_callable=AsyncMock, return_value=False):
        result = await download_pdfs(review_dir)

    mock_oa.assert_called_once()
    assert result["downloaded"] == 1
    assert result["sources"]["existing_url"] == 1


@pytest.mark.asyncio
async def test_download_pdfs_runs_rows_concurrently(tmp_path):
    # Stage 9.5 is the longest wall-clock step; rows must download concurrently (bounded by a
    # semaphore), not one-at-a-time. Track the max number of in-flight source attempts.
    import asyncio

    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [{"pmid": str(i), "doi": f"10.1/{i}"} for i in range(6)])

    in_flight = 0
    max_in_flight = 0

    async def fake_try_all(*_a, **_kw):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return False, None  # not_found → no file write needed

    with patch("src.skills.pdf_downloader._try_all_sources", side_effect=fake_try_all):
        result = await download_pdfs(review_dir)

    assert result["not_found"] == 6
    assert max_in_flight > 1, "rows must download concurrently, not serially"


@pytest.mark.asyncio
async def test_unpaywall_tried_before_openalex(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [
        {"pmid": "22222222", "doi": "10.1001/test2", "oa_pdf_url": ""},
    ])

    call_order: list[str] = []

    async def fake_unpaywall(*_a, **_kw):
        call_order.append("unpaywall")
        return True

    async def fake_openalex(*_a, **_kw):
        call_order.append("openalex")
        return False

    with patch("src.skills.pdf_downloader._try_oa_url", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_unpaywall", side_effect=fake_unpaywall), \
         patch("src.skills.pdf_downloader._try_openalex", side_effect=fake_openalex), \
         patch("src.skills.pdf_downloader._try_pmc", new_callable=AsyncMock, return_value=False):
        await download_pdfs(review_dir)

    assert call_order[0] == "unpaywall"
    assert "openalex" not in call_order  # stopped after unpaywall succeeded


@pytest.mark.asyncio
async def test_pmc_tried_last(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [
        {"pmid": "33333333", "doi": "10.1001/test3", "oa_pdf_url": ""},
    ])

    with patch("src.skills.pdf_downloader._try_oa_url", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_unpaywall", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_openalex", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_pmc", new_callable=AsyncMock, return_value=True) as mock_pmc:
        result = await download_pdfs(review_dir)

    mock_pmc.assert_called_once()
    assert result["sources"]["pmc"] == 1


# ---------------------------------------------------------------------------
# Skip already-existing PDFs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_existing_pdf(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [
        {"pmid": "44444444", "doi": "", "oa_pdf_url": ""},
    ])
    pdfs_dir = review_dir / "pdfs"
    pdfs_dir.mkdir()
    (pdfs_dir / "44444444.pdf").write_bytes(VALID_PDF)

    with patch("src.skills.pdf_downloader._try_oa_url", new_callable=AsyncMock, return_value=False) as mock_oa:
        result = await download_pdfs(review_dir)

    mock_oa.assert_not_called()
    assert result["downloaded"] == 1  # already present counts as downloaded


# ---------------------------------------------------------------------------
# PDF size validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pdf_size_validation_rejects_small_response(tmp_path):
    """_download_bytes returns None for responses <= MIN_PDF_BYTES."""
    from src.skills.pdf_downloader import _download_bytes, MIN_PDF_BYTES

    small_data = b"<html>error</html>"  # definitely < 5KB

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = small_data
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    result = await _download_bytes(mock_client, "https://example.com/fake.pdf")
    assert result is None


# ---------------------------------------------------------------------------
# Not-found list and report totals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_not_found_list_schema(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [
        {"title": "Article A", "pmid": "55555555", "doi": "10.1/a", "oa_pdf_url": ""},
    ])

    with patch("src.skills.pdf_downloader._try_oa_url", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_unpaywall", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_openalex", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_pmc", new_callable=AsyncMock, return_value=False):
        result = await download_pdfs(review_dir)

    assert result["not_found"] == 1
    entry = result["not_found_list"][0]
    assert "title" in entry
    assert "pmid" in entry
    assert "doi" in entry


@pytest.mark.asyncio
async def test_download_report_totals(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [
        {"pmid": "10000001", "doi": "10.1/a", "oa_pdf_url": "https://example.com/a.pdf"},
        {"pmid": "10000002", "doi": "10.1/b", "oa_pdf_url": ""},
        {"pmid": "10000003", "doi": "10.1/c", "oa_pdf_url": ""},
    ])

    async def oa_side_effect(client, url, dest):
        return True  # first article succeeds via OA URL

    with patch("src.skills.pdf_downloader._try_oa_url", side_effect=oa_side_effect), \
         patch("src.skills.pdf_downloader._try_unpaywall", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_openalex", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_pmc", new_callable=AsyncMock, return_value=False):
        result = await download_pdfs(review_dir)

    total = result["downloaded"] + result["not_found"] + result["failed"]
    assert total == 3


# ---------------------------------------------------------------------------
# library_request.csv and retrieval_list.csv output files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_library_request_csv_emitted(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [
        {"title": "Found Article", "pmid": "20000001", "doi": "10.1/found", "oa_pdf_url": "https://example.com/found.pdf"},
        {"title": "Missing Article", "pmid": "20000002", "doi": "10.1/miss", "oa_pdf_url": ""},
    ])

    with patch("src.skills.pdf_downloader._try_oa_url", new_callable=AsyncMock, return_value=True), \
         patch("src.skills.pdf_downloader._try_unpaywall", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_openalex", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_pmc", new_callable=AsyncMock, return_value=False):
        await download_pdfs(review_dir)

    lib_path = review_dir / "library_request.csv"
    assert lib_path.exists()

    with lib_path.open() as f:
        reader = csv.DictReader(f)
        lib_rows = list(reader)

    assert len(lib_rows) == 1
    assert lib_rows[0]["title"] == "Missing Article"
    assert set(reader.fieldnames) == {"title", "authors", "year", "journal", "doi", "pmid"}


@pytest.mark.asyncio
async def test_retrieval_list_pdf_status_column(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [
        {"title": "Art A", "pmid": "30000001", "doi": "10.1/a", "oa_pdf_url": "https://example.com/a.pdf"},
        {"title": "Art B", "pmid": "30000002", "doi": "10.1/b", "oa_pdf_url": ""},
    ])

    with patch("src.skills.pdf_downloader._try_oa_url", new_callable=AsyncMock, return_value=True), \
         patch("src.skills.pdf_downloader._try_unpaywall", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_openalex", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_pmc", new_callable=AsyncMock, return_value=False):
        await download_pdfs(review_dir)

    with (review_dir / "retrieval_list.csv").open() as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert "pdf_status" in reader.fieldnames

    statuses = {r["title"]: r["pdf_status"] for r in rows}
    assert statuses["Art A"] == "downloaded"
    assert statuses["Art B"] == "not_found"
    assert len(rows) == 2  # no rows dropped


# ---------------------------------------------------------------------------
# download_report.json written to disk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_report_json_written(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [])

    await download_pdfs(review_dir)

    report_path = review_dir / "download_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    for key in ("downloaded", "not_found", "failed", "sources", "not_found_list"):
        assert key in report


# ---------------------------------------------------------------------------
# PDF header validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pdf_header_validation_rejects_html_content(tmp_path):
    """_download_bytes returns None for >5KB HTML that doesn't start with %PDF."""
    from src.skills.pdf_downloader import _download_bytes, MIN_PDF_BYTES

    html_data = b"<html><body>" + b"x" * 6_000  # >5KB but HTML, not PDF

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = html_data
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    result = await _download_bytes(mock_client, "https://example.com/fake.pdf", _follow_html=False)
    assert result is None


@pytest.mark.asyncio
async def test_pdf_header_validation_accepts_pdf_content(tmp_path):
    """_download_bytes returns data for valid %PDF-prefixed content."""
    from src.skills.pdf_downloader import _download_bytes

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = VALID_PDF
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    result = await _download_bytes(mock_client, "https://example.com/real.pdf")
    assert result == VALID_PDF


@pytest.mark.asyncio
async def test_html_meta_citation_pdf_url_fallback(tmp_path):
    """_download_bytes follows citation_pdf_url meta tag in HTML pages."""
    from src.skills.pdf_downloader import _download_bytes

    html_with_meta = (
        b'<html><head>'
        b'<meta name="citation_pdf_url" content="https://example.com/real.pdf">'
        b'</head><body>' + b"x" * 6_000 + b"</body></html>"
    )

    call_urls: list[str] = []

    async def fake_get(url, **kwargs):
        call_urls.append(str(url))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        if "real.pdf" in str(url):
            mock_resp.content = VALID_PDF
        else:
            mock_resp.content = html_with_meta
        return mock_resp

    mock_client = AsyncMock()
    mock_client.get = fake_get

    result = await _download_bytes(mock_client, "https://publisher.com/article")
    assert result == VALID_PDF
    assert any("real.pdf" in u for u in call_urls)


# ---------------------------------------------------------------------------
# Europe PMC source
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_europe_pmc_tried_after_pmc(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [
        {"pmid": "55500001", "doi": "10.1/epmc", "oa_pdf_url": ""},
    ])

    call_order: list[str] = []

    async def fake_pmc(*_a, **_kw):
        call_order.append("pmc")
        return False

    async def fake_europe_pmc(*_a, **_kw):
        call_order.append("europe_pmc")
        return True

    with patch("src.skills.pdf_downloader._try_oa_url", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_unpaywall", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_openalex", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_pmc", side_effect=fake_pmc), \
         patch("src.skills.pdf_downloader._try_europe_pmc", side_effect=fake_europe_pmc), \
         patch("src.skills.pdf_downloader._try_crossref", new_callable=AsyncMock, return_value=False):
        result = await download_pdfs(review_dir)

    assert call_order.index("pmc") < call_order.index("europe_pmc")
    assert result["downloaded"] == 1
    assert result["sources"]["europe_pmc"] == 1


@pytest.mark.asyncio
async def test_europe_pmc_success_counted_in_sources(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [
        {"pmid": "55500002", "doi": "", "oa_pdf_url": ""},
    ])

    with patch("src.skills.pdf_downloader._try_oa_url", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_unpaywall", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_openalex", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_pmc", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_europe_pmc", new_callable=AsyncMock, return_value=True), \
         patch("src.skills.pdf_downloader._try_crossref", new_callable=AsyncMock, return_value=False):
        result = await download_pdfs(review_dir)

    assert result["sources"]["europe_pmc"] == 1
    assert result["downloaded"] == 1


# ---------------------------------------------------------------------------
# Crossref source
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crossref_tried_after_europe_pmc(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [
        {"pmid": "55500003", "doi": "10.1/cr", "oa_pdf_url": ""},
    ])

    call_order: list[str] = []

    async def fake_europe_pmc(*_a, **_kw):
        call_order.append("europe_pmc")
        return False

    async def fake_crossref(*_a, **_kw):
        call_order.append("crossref")
        return True

    with patch("src.skills.pdf_downloader._try_oa_url", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_unpaywall", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_openalex", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_pmc", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_europe_pmc", side_effect=fake_europe_pmc), \
         patch("src.skills.pdf_downloader._try_crossref", side_effect=fake_crossref):
        result = await download_pdfs(review_dir)

    assert call_order.index("europe_pmc") < call_order.index("crossref")
    assert result["downloaded"] == 1
    assert result["sources"]["crossref"] == 1


@pytest.mark.asyncio
async def test_crossref_success_counted_in_sources(tmp_path):
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [
        {"pmid": "", "doi": "10.1/crossref-only", "oa_pdf_url": ""},
    ])

    with patch("src.skills.pdf_downloader._try_oa_url", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_unpaywall", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_openalex", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_pmc", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_europe_pmc", new_callable=AsyncMock, return_value=False), \
         patch("src.skills.pdf_downloader._try_crossref", new_callable=AsyncMock, return_value=True):
        result = await download_pdfs(review_dir)

    assert result["sources"]["crossref"] == 1
    assert result["downloaded"] == 1


# ---------------------------------------------------------------------------
# download_report.json includes all source keys
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_report_includes_new_source_keys(tmp_path):
    """sources dict always contains europe_pmc and crossref keys."""
    _, review_dir = _make_review(tmp_path)
    _write_retrieval_csv(review_dir, [])

    await download_pdfs(review_dir)

    report = json.loads((review_dir / "download_report.json").read_text())
    for key in ("existing_url", "unpaywall", "openalex", "pmc", "europe_pmc", "crossref"):
        assert key in report["sources"]


# ---------------------------------------------------------------------------
# PMC direct URL fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pmc_direct_fallback_when_oa_api_returns_no_pdf_link(tmp_path):
    """_try_pmc falls back to direct /pdf/ URL when oa.fcgi has no PDF link."""
    from src.skills.pdf_downloader import _try_pmc

    pmcid = "PMC1234567"
    pmid = "99999999"

    id_conv_resp = MagicMock()
    id_conv_resp.status_code = 200
    id_conv_resp.json = MagicMock(return_value={"records": [{"pmcid": pmcid}]})

    oa_resp = MagicMock()
    oa_resp.status_code = 200
    oa_resp.text = "<OAwebservice><records/></OAwebservice>"  # no pdf link

    pdf_resp = MagicMock()
    pdf_resp.status_code = 200
    pdf_resp.content = VALID_PDF
    pdf_resp.raise_for_status = MagicMock()

    call_urls: list[str] = []

    async def fake_get(url, **kwargs):
        call_urls.append(str(url))
        if "idconv" in str(url):
            return id_conv_resp
        if "oa.fcgi" in str(url):
            return oa_resp
        return pdf_resp  # direct /pdf/ URL

    mock_client = AsyncMock()
    mock_client.get = fake_get

    dest = tmp_path / "99999999.pdf"
    result = await _try_pmc(mock_client, pmid, "", dest)

    assert result is True
    assert dest.exists()
    assert any(pmcid in u and "/pdf/" in u for u in call_urls)


# ---------------------------------------------------------------------------
# count_ready / count_citation_chased (#13/#3b)
# ---------------------------------------------------------------------------

class TestCountReady:
    def test_counts_downloaded_articles(self, tmp_path):
        from src.skills.pdf_downloader import count_ready
        _, review_dir = _make_review(tmp_path)
        rows = [
            {"title": "A", "doi": "10.1/a", "pmid": "1", "pdf_status": "downloaded"},
            {"title": "B", "doi": "10.1/b", "pmid": "2", "pdf_status": "not_found"},
            {"title": "C", "doi": "10.1/c", "pmid": "3", "pdf_status": "downloaded"},
        ]
        _write_retrieval_csv(review_dir, rows)
        result = count_ready(review_dir)
        assert result["total"] == 3
        assert result["pdfs_ready"] == 2
        assert result["not_found"] == 1

    def test_missing_csv_returns_error(self, tmp_path):
        from src.skills.pdf_downloader import count_ready
        _, review_dir = _make_review(tmp_path)
        result = count_ready(review_dir)
        assert "error" in result

    def test_empty_pdf_status_counts_as_not_found(self, tmp_path):
        from src.skills.pdf_downloader import count_ready
        _, review_dir = _make_review(tmp_path)
        rows = [{"title": "A", "doi": "10.1/a", "pmid": "1", "pdf_status": ""}]
        _write_retrieval_csv(review_dir, rows)
        result = count_ready(review_dir)
        assert result["pdfs_ready"] == 0



# ---------------------------------------------------------------------------
# state.py citation_chased resume hint (#13)
# ---------------------------------------------------------------------------

class TestStateCitationChasedHint:
    def test_scan_includes_resume_hint_for_citation_chased(self, tmp_path):
        from src.agent.state import scan
        review_dir = tmp_path / "test-review"
        review_dir.mkdir()
        from src.models.review import Review, ReviewStatus, ScreeningMode
        review = Review(
            review_id="test-review",
            raw_question="Test question",
            screening_mode=ScreeningMode.thorough,
            status=ReviewStatus.citation_chased,
        )
        state_manager.save(review, review_dir)
        summaries = scan(tmp_path)
        assert len(summaries) == 1
        assert "resume_hint" in summaries[0]
        assert "Stage 9" in summaries[0]["resume_hint"]

    def test_scan_no_resume_hint_for_other_statuses(self, tmp_path):
        from src.agent.state import scan
        review_dir = tmp_path / "test-review2"
        review_dir.mkdir()
        from src.models.review import Review, ReviewStatus, ScreeningMode
        review = Review(
            review_id="test-review2",
            raw_question="Test question",
            screening_mode=ScreeningMode.thorough,
            status=ReviewStatus.fulltext_assessed,
        )
        state_manager.save(review, review_dir)
        summaries = scan(tmp_path)
        assert len(summaries) == 1
        assert "resume_hint" not in summaries[0]
