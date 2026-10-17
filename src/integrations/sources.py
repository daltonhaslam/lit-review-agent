"""Search-source identifiers shared by the retriever and the report writers.

Kept free of client imports so output modules can name sources without loading httpx
and every API client.
"""

from __future__ import annotations

DEFAULT_SOURCES = ["pubmed", "openalex", "semantic_scholar"]

SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "pubmed": "PubMed",
    "openalex": "OpenAlex",
    "semantic_scholar": "Semantic Scholar",
    "europepmc": "Europe PMC",
}


def display_names(sources: list[str] | None) -> list[str]:
    """Display names for source ids, falling back to DEFAULT_SOURCES when none are recorded."""
    return [SOURCE_DISPLAY_NAMES.get(s, s) for s in (sources or DEFAULT_SOURCES)]
