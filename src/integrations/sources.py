"""Search-source identifiers shared by the retriever and the report writers.

Kept free of client imports so output modules can name sources without loading httpx
and every API client.
"""

DEFAULT_SOURCES = ["pubmed", "openalex", "semantic_scholar"]

SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "pubmed": "PubMed",
    "openalex": "OpenAlex",
    "semantic_scholar": "Semantic Scholar",
    "europepmc": "Europe PMC",
}
