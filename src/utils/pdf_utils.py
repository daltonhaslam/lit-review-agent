"""Shared PDF validation constants used by download utilities."""
from typing import Optional
import re

MIN_PDF_BYTES = 5_120
_RE_CITATION_META = re.compile(rb"<meta\b[^>]*citation_pdf_url[^>]*>", re.IGNORECASE)
_RE_CONTENT_ATTR = re.compile(rb'\bcontent=["\']([^"\']+)["\']', re.IGNORECASE)


def extract_citation_pdf_url(html_bytes: bytes) -> Optional[str]:
    """Extract the URL from <meta name="citation_pdf_url" content="..."> if present."""
    meta_match = _RE_CITATION_META.search(html_bytes)
    if not meta_match:
        return None
    content_match = _RE_CONTENT_ATTR.search(meta_match.group(0))
    if not content_match:
        return None
    return content_match.group(1).decode("utf-8", errors="replace").strip() or None
