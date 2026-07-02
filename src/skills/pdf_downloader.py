"""Automated PDF download for articles in the retrieval list (Stage 9.5).

Attempts to download open-access PDFs before the user submits a library request.
Source priority per article:
  1. Existing oa_pdf_url in retrieval_list.csv (direct download)
  2. Unpaywall  (https://api.unpaywall.org/v2/{doi}?email=...)
  3. OpenAlex   (https://api.openalex.org/works?filter=doi:{doi})
  4. PMC OA     (NCBI ID converter → PMC OA service → direct URL fallback)
  5. Europe PMC (https://www.ebi.ac.uk/europepmc/webservices/rest/search)
  6. Crossref   (https://api.crossref.org/works/{doi})

Not open-access: articles unresolved by the waterfall above land in
library_request.csv for manual download (e.g. via institutional library
access), then re-imported with --import-from / --import-from-downloads.

Outputs:
  reviews/{slug}/pdfs/*.pdf            — downloaded files
  reviews/{slug}/retrieval_list.csv    — updated in-place with pdf_status column
  reviews/{slug}/library_request.csv  — not-found subset for hospital library
  reviews/{slug}/download_report.json — summary statistics

Input:  review_dir (reads retrieval_list.csv; status must be retrieval_list_generated)
Output: pdfs/; retrieval_list.csv updated; library_request.csv; download_report.json
        status=pdfs_downloaded
CLI:    python -m src.skills.pdf_downloader --review-dir reviews/{slug}
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Optional
import defusedxml.ElementTree as ElementTree

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import httpx

from src.agent import state as state_manager
from src.integrations.config import load_api_config
from src.models.review import ReviewStatus
from src.skills._base import iter_pdfs
from src.utils.filename_utils import _normalize_doi_for_filename
from src.utils.pdf_utils import MIN_PDF_BYTES, _RE_CITATION_META, _RE_CONTENT_ATTR
from src.utils.rate_limiter import LIMITERS, RateLimiter

_DOWNLOAD_TIMEOUT = 60.0
_GENERIC_LIMITER = RateLimiter(rate=5.0)  # for PDF downloads from publisher domains
# Stage 9.5 concurrent-download cap. Per-domain rate limiters still throttle actual requests;
# this just lets independent articles' source cascades overlap instead of running strictly serial.
_DOWNLOAD_CONCURRENCY = 6

LIBRARY_COLUMNS = ["title", "authors", "year", "journal", "doi", "pmid"]
_PDF_SOURCES = ("existing_url", "unpaywall", "openalex", "pmc", "europe_pmc", "crossref")


def _atomic_replace_csv(
    path: Path,
    rows: list[dict],
    fieldnames: list[str],
    *,
    expected_min_rows: int = 0,
) -> None:
    """Issue #56: atomically replace a CSV. Writes to {path}.tmp, then renames.
    If `expected_min_rows > 0` and `len(rows)` is below it, raises ValueError
    BEFORE touching the destination — defends against partial-row bugs that
    would silently truncate retrieval_list.csv (observed corruption from a previous
    retry-path bug)."""
    if expected_min_rows > 0 and len(rows) < expected_min_rows:
        raise ValueError(
            f"refusing to write {len(rows)} rows to {path.name}: "
            f"expected at least {expected_min_rows}. Aborting to preserve existing state."
        )
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def _dest_path(pdfs_dir: Path, pmid: Optional[str], doi: Optional[str]) -> Optional[Path]:
    if pmid:
        return pdfs_dir / f"{pmid}.pdf"
    if doi:
        return pdfs_dir / f"{_normalize_doi_for_filename(doi)}.pdf"
    return None


def is_pdf_bytes(data: bytes) -> bool:
    """Issue #46: Validate that response body is a real PDF by magic-byte check.
    The first 5 bytes must be `%PDF-`. Empty, short, or HTML responses fail.
    Used by all PDF write sites in this module.
    """
    return len(data) >= 5 and data[:5] == b"%PDF-"


def validate_disk(review_dir: Path) -> dict:
    """Issue #46: walk pdfs/, check magic bytes on every *.pdf file, and move
    any non-PDF to pdfs/_rejected/. Updates retrieval_list.csv to mark each
    quarantined file's row as pdf_status=failed."""
    pdfs_dir = review_dir / "pdfs"
    if not pdfs_dir.exists():
        return {"validated": 0, "rejected": []}
    rejected_dir = pdfs_dir / "_rejected"
    rejected: list[dict] = []
    validated = 0
    for pdf_path in iter_pdfs(pdfs_dir):
        validated += 1
        try:
            with pdf_path.open("rb") as fh:
                head = fh.read(5)
        except OSError:
            continue
        if is_pdf_bytes(head):
            continue
        rejected_dir.mkdir(parents=True, exist_ok=True)
        target = rejected_dir / pdf_path.name
        pdf_path.rename(target)
        rejected.append({"filename": pdf_path.name, "reason": "non-pdf-content"})

    if rejected:
        _mark_rows_failed(review_dir, {r["filename"] for r in rejected})
    return {"validated": validated, "rejected": rejected}


def _mark_rows_failed(review_dir: Path, rejected_filenames: set[str]) -> None:
    """Flip retrieval_list.csv rows whose pdf_path basename is in the rejected
    set to pdf_status=failed, pdf_path=''."""
    retrieval_path = review_dir / "retrieval_list.csv"
    if not retrieval_path.exists():
        return
    with retrieval_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    changed = False
    for row in rows:
        path = row.get("pdf_path") or ""
        if path and Path(path).name in rejected_filenames:
            row["pdf_status"] = "failed"
            row["pdf_path"] = ""
            changed = True
    if changed:
        # Issue #56: only flips pdf_status/pdf_path; row count never shrinks.
        _atomic_replace_csv(retrieval_path, rows, fieldnames, expected_min_rows=len(rows))


# IGNORECASE so an uppercase `.PDF` extension (Windows-origin / some publishers) still matches.
_ELSEVIER_PII_RE = re.compile(r"^1-s2\.0-([A-Z0-9]+)-main\.pdf$", re.IGNORECASE)
_BARE_PMID_RE = re.compile(r"^(\d+)\.pdf$", re.IGNORECASE)


def _match_filename_to_article(
    filename: str, retrieval_rows: list[dict]
) -> tuple[Optional[dict], str]:
    """Issue #47 / #44: deterministic filename → retrieval row matcher.

    Returns (matched_row, method) on success or (None, reason) on failure.
    The match cascade (in order):
      1. Bare PMID: `{digits}.pdf`
      2. DOI in filename: matches `_normalize_doi_for_filename(doi).pdf`
      3. Elsevier PII: `1-s2.0-{PII}-main.pdf` — defers to caller for PMID resolution
    """
    name = Path(filename).name
    if not name.lower().endswith(".pdf"):
        return None, "not a .pdf file"

    m = _BARE_PMID_RE.match(name)
    if m:
        pmid = m.group(1)
        for row in retrieval_rows:
            if (row.get("pmid") or "").strip() == pmid:
                return row, "pmid_in_filename"
        return None, f"PMID {pmid} not in retrieval_list"

    stem = name[:-4].lower()  # strip ".pdf"; lowercase to match _normalize_doi_for_filename
    for row in retrieval_rows:
        doi = (row.get("doi") or "").strip()
        if doi and _normalize_doi_for_filename(doi) == stem:
            return row, "doi_in_filename"

    pii_match = _ELSEVIER_PII_RE.match(name)
    if pii_match:
        return None, f"elsevier_pii:{pii_match.group(1)}"

    return None, "no pattern matched"


def reconcile_disk(review_dir: Path) -> dict:
    """Issue #47: walk pdfs/, match each file to a retrieval_list.csv row, and
    flip pdf_status=downloaded for any row whose file is on disk but marked
    not_found / failed / skipped / blank. Reports orphans (files with no row).
    """
    retrieval_path = review_dir / "retrieval_list.csv"
    pdfs_dir = review_dir / "pdfs"
    if not retrieval_path.exists() or not pdfs_dir.exists():
        return {"reconciled": 0, "already_correct": 0, "no_match_in_csv": []}

    with retrieval_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {"reconciled": 0, "already_correct": 0, "no_match_in_csv": []}
    fieldnames = list(rows[0].keys())
    if "pdf_path" not in fieldnames:
        fieldnames.append("pdf_path")

    reconciled = already_correct = 0
    no_match: list[str] = []

    for pdf_path in iter_pdfs(pdfs_dir):
        # iter_pdfs is top-level only, so quarantined files in pdfs/_rejected/ are never yielded.
        matched, _method = _match_filename_to_article(pdf_path.name, rows)
        if matched is None:
            no_match.append(pdf_path.name)
            continue
        rel_path = f"pdfs/{pdf_path.name}"
        if matched.get("pdf_status") == "downloaded" and matched.get("pdf_path") == rel_path:
            already_correct += 1
            continue
        matched["pdf_status"] = "downloaded"
        matched["pdf_path"] = rel_path
        reconciled += 1

    if reconciled > 0:
        # Issue #56: reconcile_disk only flips pdf_status; row count never shrinks.
        _atomic_replace_csv(retrieval_path, rows, fieldnames, expected_min_rows=len(rows))

    return {
        "reconciled": reconciled,
        "already_correct": already_correct,
        "no_match_in_csv": no_match,
    }


def _extract_doi_from_pdf_first_page(pdf_path: Path) -> Optional[str]:
    """Fallback for filename matcher: parse the first page of the PDF and
    regex-extract a DOI. Uses pypdf (already a dependency via fulltext_assessor).
    Returns None on parse failure or no match — never raises."""
    try:
        import pypdf
        reader = pypdf.PdfReader(str(pdf_path))
        if not reader.pages:
            return None
        text = reader.pages[0].extract_text() or ""
        m = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, re.IGNORECASE)
        return m.group(0) if m else None
    except Exception:
        return None


def import_from(review_dir: Path, source_dir: Path) -> dict:
    """Issue #44 Stage 9.6: walk source_dir for *.pdf files, identify each via
    `_match_filename_to_article` (or PDF-text DOI fallback), rename to the
    canonical PMID/DOI form, validate via is_pdf_bytes, then MOVE to pdfs/.

    Unmatched files (no PMID/DOI match) and non-PDF content stay in source_dir
    and appear in the returned `unmatched` list. After moves, runs
    reconcile_disk so retrieval_list.csv reflects the new state.
    """
    retrieval_path = review_dir / "retrieval_list.csv"
    pdfs_dir = review_dir / "pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    if not retrieval_path.exists():
        return {"imported_count": 0, "imported": [], "unmatched": [], "still_missing": 0, "error": "retrieval_list.csv not found"}

    with retrieval_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    imported: list[dict] = []
    unmatched: list[dict] = []

    for src_pdf in iter_pdfs(source_dir):
        # Magic-bytes guard before any rename/move.
        try:
            with src_pdf.open("rb") as fh:
                head = fh.read(5)
        except OSError:
            unmatched.append({"filename": src_pdf.name, "reason": "unreadable"})
            continue
        if not is_pdf_bytes(head):
            unmatched.append({"filename": src_pdf.name, "reason": "non-pdf-content"})
            continue

        matched, method = _match_filename_to_article(src_pdf.name, rows)
        if matched is None:
            # PDF text DOI fallback (only if filename matching failed)
            doi_from_text = _extract_doi_from_pdf_first_page(src_pdf)
            if doi_from_text:
                for row in rows:
                    if (row.get("doi") or "").strip().lower() == doi_from_text.lower():
                        matched = row
                        method = "doi_from_pdf_text"
                        break
        if matched is None:
            unmatched.append({"filename": src_pdf.name, "reason": method})
            continue

        dest = _dest_path(pdfs_dir, matched.get("pmid"), matched.get("doi"))
        if dest is None:
            unmatched.append({"filename": src_pdf.name, "reason": "row has no pmid or doi"})
            continue
        # shutil.move handles cross-filesystem renames (which os.rename cannot).
        shutil.move(str(src_pdf), str(dest))
        imported.append({"src": src_pdf.name, "dest": dest.name, "method": method})

    reconcile_summary = reconcile_disk(review_dir)
    # Re-read after reconcile so still_missing reflects the post-reconcile state.
    with retrieval_path.open(encoding="utf-8") as f:
        rows_after = list(csv.DictReader(f))
    still_missing = sum(
        1 for row in rows_after
        if row.get("pdf_status") not in ("downloaded",) and (row.get("pmid") or row.get("doi"))
    )
    return {
        "imported_count": len(imported),
        "imported": imported,
        "unmatched": unmatched,
        "still_missing": still_missing,
        "reconcile_summary": reconcile_summary,
    }


async def _download_bytes(
    client: httpx.AsyncClient, url: str, *, _follow_html: bool = True
) -> Optional[bytes]:
    try:
        async def _get() -> bytes:
            resp = await client.get(url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT)
            if resp.status_code in (403, 404):
                return b""
            resp.raise_for_status()
            return resp.content

        data = await RateLimiter.retry_with_backoff(_get, max_retries=2, base_delay=1.0)
        if not data or len(data) < MIN_PDF_BYTES:
            return None
        if is_pdf_bytes(data):
            return data
        # HTML page — attempt citation_pdf_url meta tag fallback (once)
        if _follow_html and data.lstrip().startswith(b"<"):
            meta_match = _RE_CITATION_META.search(data)
            if meta_match:
                content_match = _RE_CONTENT_ATTR.search(meta_match.group(0))
                if content_match:
                    meta_url = content_match.group(1).decode(errors="ignore").strip()
                    if meta_url:
                        await _GENERIC_LIMITER.acquire()
                        return await _download_bytes(client, meta_url, _follow_html=False)
        return None
    except Exception:
        return None


async def _try_oa_url(
    client: httpx.AsyncClient, url: str, dest: Path
) -> bool:
    await _GENERIC_LIMITER.acquire()
    data = await _download_bytes(client, url)
    if not data:
        return False
    try:
        dest.write_bytes(data)
        return True
    except OSError:
        return False


async def _try_unpaywall(
    client: httpx.AsyncClient, doi: str, email: str, dest: Path
) -> bool:
    if not doi or not email:
        return False
    await LIMITERS["api.unpaywall.org"].acquire()
    try:
        url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
        resp = await client.get(url, timeout=15.0)
        if resp.status_code != 200:
            return False
        payload = resp.json()
        best = payload.get("best_oa_location") or {}
        pdf_url = best.get("url_for_pdf")
        if not pdf_url:
            return False
    except Exception:
        return False
    await _GENERIC_LIMITER.acquire()
    data = await _download_bytes(client, pdf_url)
    if not data:
        return False
    try:
        dest.write_bytes(data)
        return True
    except OSError:
        return False


async def _try_openalex(
    client: httpx.AsyncClient, doi: str, email: str, dest: Path
) -> bool:
    if not doi:
        return False
    await LIMITERS["api.openalex.org"].acquire()
    try:
        params = {"filter": f"doi:{doi}"}
        if email:
            params["mailto"] = email
        resp = await client.get("https://api.openalex.org/works", params=params, timeout=15.0)
        if resp.status_code != 200:
            return False
        results = resp.json().get("results", [])
        if not results:
            return False
        best = (results[0].get("best_oa_location") or {})
        pdf_url = best.get("pdf_url")
        if not pdf_url:
            return False
    except Exception:
        return False
    await _GENERIC_LIMITER.acquire()
    data = await _download_bytes(client, pdf_url)
    if not data:
        return False
    try:
        dest.write_bytes(data)
        return True
    except OSError:
        return False


async def _try_pmc(
    client: httpx.AsyncClient, pmid: str, ncbi_key: str, dest: Path
) -> bool:
    if not pmid:
        return False
    await LIMITERS["eutils.ncbi.nlm.nih.gov"].acquire()
    try:
        params: dict = {"ids": pmid, "format": "json"}
        if ncbi_key:
            params["api_key"] = ncbi_key
        resp = await client.get(
            "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/",
            params=params,
            timeout=15.0,
        )
        if resp.status_code != 200:
            return False
        records = resp.json().get("records", [])
        pmcid = records[0].get("pmcid") if records else None
        if not pmcid:
            return False
    except Exception:
        return False

    await LIMITERS["eutils.ncbi.nlm.nih.gov"].acquire()
    pdf_link = None
    try:
        resp = await client.get(
            "https://pmc.ncbi.nlm.nih.gov/utils/oa/oa.fcgi",
            params={"id": pmcid},
            timeout=15.0,
        )
        if resp.status_code == 200:
            root = ElementTree.fromstring(resp.text)
            for link in root.iter("link"):
                if link.get("format") == "pdf":
                    pdf_link = link.get("href")
                    break
    except Exception:
        pass

    # Direct URL fallback for "Free PMC Article" entries not in the OA subset
    if not pdf_link:
        pdf_link = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"

    await _GENERIC_LIMITER.acquire()
    data = await _download_bytes(client, pdf_link)
    if not data:
        return False
    try:
        dest.write_bytes(data)
        return True
    except OSError:
        return False


async def _try_europe_pmc(
    client: httpx.AsyncClient, pmid: str, dest: Path
) -> bool:
    if not pmid:
        return False
    await LIMITERS["www.ebi.ac.uk"].acquire()
    try:
        params = {"query": f"EXT_ID:{pmid}", "format": "json", "resultType": "core"}
        resp = await client.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params=params,
            timeout=15.0,
        )
        if resp.status_code != 200:
            return False
        results = resp.json().get("resultList", {}).get("result", [])
        if not results:
            return False
        full_text_urls = results[0].get("fullTextUrlList", {}).get("fullTextUrl", [])
        # Prefer documentStyle == "pdf"; fall back to any Open access entry
        pdf_url = None
        for entry in full_text_urls:
            if entry.get("documentStyle") == "pdf":
                pdf_url = entry.get("url")
                break
        if not pdf_url:
            for entry in full_text_urls:
                if entry.get("availability") == "Open access":
                    pdf_url = entry.get("url")
                    break
        if not pdf_url:
            return False
    except Exception:
        return False
    await _GENERIC_LIMITER.acquire()
    data = await _download_bytes(client, pdf_url)
    if not data:
        return False
    try:
        dest.write_bytes(data)
        return True
    except OSError:
        return False


async def _try_crossref(
    client: httpx.AsyncClient, doi: str, dest: Path
) -> bool:
    if not doi:
        return False
    await LIMITERS["api.crossref.org"].acquire()
    try:
        resp = await client.get(
            f"https://api.crossref.org/works/{doi}",
            timeout=15.0,
        )
        if resp.status_code != 200:
            return False
        links = resp.json().get("message", {}).get("link", [])
        pdf_url = None
        for link in links:
            ct = link.get("content-type", "")
            ia = link.get("intended-application", "")
            if ct == "application/pdf" or ia == "text-mining":
                pdf_url = link.get("URL")
                break
        if not pdf_url:
            return False
    except Exception:
        return False
    await _GENERIC_LIMITER.acquire()
    data = await _download_bytes(client, pdf_url)
    if not data:
        return False
    try:
        dest.write_bytes(data)
        return True
    except OSError:
        return False


async def _try_all_sources(
    client: httpx.AsyncClient,
    dest: Path,
    pmid: str | None,
    doi: str | None,
    oa_pdf_url: str | None,
    cfg,
) -> tuple[bool, str | None]:
    if oa_pdf_url and await _try_oa_url(client, oa_pdf_url, dest):
        return True, "existing_url"
    if doi:
        if await _try_unpaywall(client, doi, cfg.unpaywall_email, dest):
            return True, "unpaywall"
        if await _try_openalex(client, doi, cfg.openalex_email, dest):
            return True, "openalex"
    if pmid:
        if await _try_pmc(client, pmid, cfg.ncbi_api_key, dest):
            return True, "pmc"
        if await _try_europe_pmc(client, pmid, dest):
            return True, "europe_pmc"
    if doi and await _try_crossref(client, doi, dest):
        return True, "crossref"
    return False, None


async def download_pdfs(review_dir: Path) -> dict:
    review = state_manager.load(review_dir)
    if review.status != ReviewStatus.retrieval_list_generated:
        raise ValueError(
            f"Expected status 'retrieval_list_generated', got '{review.status}'."
        )

    csv_path = review_dir / "retrieval_list.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"retrieval_list.csv not found in {review_dir}")

    pdfs_dir = review_dir / "pdfs"
    pdfs_dir.mkdir(exist_ok=True)

    cfg = load_api_config()

    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        empty_report = {
            "downloaded": 0, "not_found": 0, "failed": 0,
            "sources": {
                "existing_url": 0, "unpaywall": 0, "openalex": 0,
                "pmc": 0, "europe_pmc": 0, "crossref": 0,
            },
            "not_found_list": [],
        }
        (review_dir / "download_report.json").write_text(json.dumps(empty_report, indent=2))
        with (review_dir / "library_request.csv").open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=LIBRARY_COLUMNS).writeheader()
        review.status = ReviewStatus.pdfs_downloaded
        state_manager.save(review, review_dir)
        return empty_report

    sources_count: dict[str, int] = dict.fromkeys(_PDF_SOURCES, 0)
    downloaded = 0
    not_found = 0
    failed = 0
    not_found_list: list[dict] = []
    library_rows: list[dict] = []

    async with httpx.AsyncClient(
        headers={"User-Agent": "LitReviewAgent/1.0 (research tool)"},
        follow_redirects=True,
    ) as client:
        # Process rows concurrently (bounded) — this is the longest wall-clock step and each row's
        # per-source round-trips are independent. Per-domain rate limiters are process-global
        # singletons, so throttling stays correct under concurrency; each coroutine mutates only
        # its own row and writes only its own unique pmid/doi-keyed dest file (no shared-file race).
        sem = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)

        async def _process(row: dict) -> tuple[str, Optional[str], Optional[dict]]:
            """Returns (status, source_used, library_row). Mutates row['pdf_status'] in place."""
            pmid = row.get("pmid", "").strip() or None
            doi = row.get("doi", "").strip() or None
            oa_pdf_url = row.get("oa_pdf_url", "").strip() or None

            dest = _dest_path(pdfs_dir, pmid, doi)
            if dest is None:
                row["pdf_status"] = "skipped"
                return "skipped", None, None
            if dest.exists():
                row["pdf_status"] = "downloaded"
                return "downloaded", None, None

            async with sem:
                success, source_used = await _try_all_sources(
                    client, dest, pmid, doi, oa_pdf_url, cfg,
                )

            if success and source_used:
                row["pdf_status"] = "downloaded"
                return "downloaded", source_used, None
            if dest.exists():
                # write_bytes succeeded but dest was somehow left behind — treat as failed
                dest.unlink(missing_ok=True)
                row["pdf_status"] = "failed"
                return "failed", None, None
            row["pdf_status"] = "not_found"
            return "not_found", None, {k: row.get(k, "") for k in LIBRARY_COLUMNS}

        # gather preserves input order in results, so counters/lists stay deterministic.
        results = await asyncio.gather(*(_process(row) for row in rows))

    for (status, source_used, library_row), row in zip(results, rows):
        if status == "downloaded":
            downloaded += 1
            if source_used:
                sources_count[source_used] += 1
        elif status == "failed":
            failed += 1
        elif status == "not_found":
            not_found += 1
            not_found_list.append(
                {"title": row.get("title", ""), "pmid": (row.get("pmid") or "").strip(), "doi": (row.get("doi") or "").strip()}
            )
            library_rows.append(library_row)

    # Update retrieval_list.csv in-place with pdf_status column.
    # Issue #56: download_pdfs only mutates pdf_status — row count never legitimately
    # shrinks, so refuse to write fewer rows than were read.
    fieldnames = list(rows[0].keys())
    _atomic_replace_csv(csv_path, rows, fieldnames, expected_min_rows=len(rows))

    # Write library_request.csv (not_found only — accumulated during loop)
    _atomic_replace_csv(review_dir / "library_request.csv", library_rows, LIBRARY_COLUMNS)

    report = {
        "downloaded": downloaded,
        "not_found": not_found,
        "failed": failed,
        "sources": sources_count,
        "not_found_list": not_found_list,
    }
    (review_dir / "download_report.json").write_text(json.dumps(report, indent=2))

    review.status = ReviewStatus.pdfs_downloaded
    state_manager.save(review, review_dir)

    return report


def count_ready(review_dir: Path) -> dict:
    """Count PDFs ready for primary-search articles (no download)."""
    csv_path = review_dir / "retrieval_list.csv"
    if not csv_path.exists():
        return {"error": "retrieval_list.csv not found"}
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    total = len(rows)
    ready = sum(1 for r in rows if r.get("pdf_status") == "downloaded")
    return {"total": total, "pdfs_ready": ready, "not_found": total - ready}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download open-access PDFs for retrieval list (Stage 9.5)"
    )
    parser.add_argument("--review-dir", required=True, metavar="DIR",
                        help="Review directory (reads retrieval_list.csv)")
    parser.add_argument("--count-ready", action="store_true",
                        help="Count PDFs ready for primary-search articles (no download)")
    parser.add_argument("--validate-disk", action="store_true",
                        help="Walk pdfs/ and quarantine any non-PDF files (Issue #46)")
    parser.add_argument("--reconcile-disk", action="store_true",
                        help="Walk pdfs/ and update retrieval_list.csv pdf_status to match disk (Issue #47)")
    parser.add_argument("--import-from", metavar="PATH", default=None,
                        help="Walk PATH for *.pdf files, match + rename + move to pdfs/, then reconcile (Stage 9.6 / Issue #44)")
    parser.add_argument("--import-from-downloads", action="store_true",
                        help="Shorthand: --import-from $HOME/Downloads")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)

    if args.validate_disk:
        result = validate_disk(review_dir)
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if args.reconcile_disk:
        result = reconcile_disk(review_dir)
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if args.import_from or args.import_from_downloads:
        source = Path(args.import_from) if args.import_from else Path.home() / "Downloads"
        if not source.exists():
            print(json.dumps({"error": f"source directory not found: {source}"}))
            sys.exit(1)
        result = import_from(review_dir, source)
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if args.count_ready:
        print(json.dumps(count_ready(review_dir), indent=2))
    else:
        async def _main() -> None:
            try:
                result = await download_pdfs(review_dir)
                print(json.dumps(result, indent=2))
            except Exception as exc:
                print(json.dumps({"error": str(exc)}))
                sys.exit(1)
        asyncio.run(_main())
