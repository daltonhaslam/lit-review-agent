"""Quick connectivity check for all 6 APIs. Run from project root:

    python3 tests/test_api_connectivity.py

Makes live calls, so under pytest these are marked ``network`` and skipped unless
LIT_REVIEW_NETWORK_TESTS=1 is set (see tests/conftest.py).
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from src.integrations.config import load_api_config

cfg = load_api_config()
TIMEOUT = 15.0
TEST_DOI = "10.1016/j.ijmedinf.2015.02.002"  # Paré & Kitsiou 2015
TEST_PMID = "25835562"

pytestmark = pytest.mark.network


async def check(name: str, coro):
    try:
        result = await coro
        print(f"  ✓  {name}: {result}")
        return True
    except Exception as e:
        print(f"  ✗  {name}: {e}")
        return False


async def test_pubmed():
    params = {
        "db": "pubmed", "term": "heart failure readmission",
        "retmax": "3", "retmode": "json",
    }
    if cfg.ncbi_api_key:
        params["api_key"] = cfg.ncbi_api_key
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params)
        r.raise_for_status()
        count = r.json()["esearchresult"]["count"]
        key_status = "with API key" if cfg.ncbi_api_key else "NO KEY (3 req/s)"
        return f"{count} results | {key_status}"


async def test_openalex():
    params = {"search": "hospital readmission", "filter": "type:article", "per-page": "1",
               "mailto": cfg.openalex_email}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get("https://api.openalex.org/works", params=params)
        r.raise_for_status()
        count = r.json()["meta"]["count"]
        return f"{count:,} total works matching query"


async def test_crossref():
    params = {"query": "readmission heart failure", "rows": "1",
               "mailto": cfg.crossref_mailto}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get("https://api.crossref.org/works", params=params)
        r.raise_for_status()
        total = r.json()["message"]["total-results"]
        return f"{total:,} total works matching query"


async def test_semantic_scholar():
    # Batch lookup by PMID
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(
            "https://api.semanticscholar.org/graph/v1/paper/batch",
            params={"fields": "title,year,citationCount"},
            json={"ids": [f"PMID:{TEST_PMID}"]},
        )
        r.raise_for_status()
        data = r.json()
        paper = data[0] if data else {}
        return f"'{paper.get('title', '')[:50]}' ({paper.get('year')}) — {paper.get('citationCount')} citations"


async def test_unpaywall():
    url = f"https://api.unpaywall.org/v2/{TEST_DOI}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(url, params={"email": cfg.unpaywall_email})
        r.raise_for_status()
        data = r.json()
        oa = data.get("is_oa")
        title = data.get("title", "")[:50]
        return f"'{title}' | is_oa={oa}"


async def test_europepmc():
    params = {"query": "heart failure readmission", "resulttype": "core",
               "pageSize": "1", "format": "json"}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params=params)
        r.raise_for_status()
        hit_count = r.json()["hitCount"]
        return f"{hit_count:,} results"


async def main():
    print("\nAPI Connectivity Check\n" + "=" * 40)
    results = await asyncio.gather(
        check("PubMed          ", test_pubmed()),
        check("OpenAlex        ", test_openalex()),
        check("CrossRef        ", test_crossref()),
        check("Semantic Scholar", test_semantic_scholar()),
        check("Unpaywall       ", test_unpaywall()),
        check("Europe PMC      ", test_europepmc()),
        return_exceptions=False,
    )
    passed = sum(results)
    print(f"\n{passed}/6 APIs reachable")
    if passed < 6:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
