from __future__ import annotations

import re


def _normalize_doi_for_filename(doi: str) -> str:
    """Normalize a DOI string to a safe filename stem.

    Strips the https://doi.org/ prefix, lowercases, and replaces filesystem-unsafe
    characters with underscores. Both fulltext_assessor and pdf_downloader use this
    to ensure PDFs written by the downloader are found by the assessor.
    """
    doi = re.sub(r"^https?://doi\.org/", "", doi.lower())
    return re.sub(r'[/\\:*?"<>|]', "_", doi)
