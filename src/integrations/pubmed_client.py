"""PubMed E-utilities client.

Implements:
  - search(query, max_results, filters) → list[str] (PMIDs)
  - fetch_abstracts(pmids) → list[Article]
  - mesh_lookup(term) → dict with entry_terms, tree_number, year_introduced
"""
import asyncio
import json
import re
from typing import Optional
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as ET

import httpx

from ..models.article import Article
from ..utils.rate_limiter import LIMITERS, RateLimiter

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
LIMITER = LIMITERS["eutils.ncbi.nlm.nih.gov"]


class PubMedClient:
    def __init__(self, api_key: str = ""):
        self._api_key = api_key

    def _base_params(self) -> dict:
        params = {}
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    async def _get(self, endpoint: str, params: dict) -> str:
        params.update(self._base_params())
        await LIMITER.acquire()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{BASE_URL}/{endpoint}", params=params)
            resp.raise_for_status()
            return resp.text

    async def search(
        self,
        query: str,
        max_results: int = 500,
        filters: Optional[list[str]] = None,
        use_history: bool = True,
    ) -> dict:
        """Run esearch; returns {pmids, web_env, query_key, count}."""
        term = query
        if filters:
            term = f"({term}) AND " + " AND ".join(f"({f})" for f in filters)

        params = {
            "db": "pubmed",
            "term": term,
            "retmax": str(max_results),
            "retmode": "json",
            "usehistory": "y" if use_history else "n",
        }
        raw = await RateLimiter.retry_with_backoff(lambda: self._get("esearch.fcgi", params))
        data = json.loads(raw)["esearchresult"]

        return {
            "pmids": data.get("idlist", []),
            "web_env": data.get("webenv", ""),
            "query_key": data.get("querykey", ""),
            "count": int(data.get("count", 0)),
        }

    async def fetch_abstracts(self, pmids: list[str]) -> list[Article]:
        """Batch efetch for up to 10,000 PMIDs; returns Article list."""
        if not pmids:
            return []

        articles: list[Article] = []
        batch_size = 200  # NCBI recommended batch size for efetch
        for i in range(0, len(pmids), batch_size):
            batch = pmids[i : i + batch_size]
            params = {
                "db": "pubmed",
                "id": ",".join(batch),
                "rettype": "abstract",
                "retmode": "xml",
            }
            raw = await RateLimiter.retry_with_backoff(
                lambda: self._get("efetch.fcgi", params)
            )
            articles.extend(_parse_pubmed_xml(raw))
            await asyncio.sleep(0.1)  # small buffer between batches

        return articles

    async def search_by_title(self, title: str) -> Optional[str]:
        """Exact title search; returns PMID only if exactly one match (avoids false positives)."""
        result = await self.search(f'"{title}"[Title]', max_results=2, use_history=False)
        pmids = result["pmids"]
        return pmids[0] if len(pmids) == 1 else None

    async def mesh_lookup(self, term: str) -> dict:
        """Return MeSH entry for a term: entry_terms, tree_number, year_introduced."""
        # Step 1: find MeSH UID
        search_params = {"db": "mesh", "term": term, "retmode": "json", "retmax": "1"}
        raw = await RateLimiter.retry_with_backoff(
            lambda: self._get("esearch.fcgi", search_params)
        )
        result = json.loads(raw)["esearchresult"]
        ids = result.get("idlist", [])
        if not ids:
            return {"term": term, "found": False}

        # Step 2: fetch MeSH record
        fetch_params = {"db": "mesh", "id": ids[0], "retmode": "xml"}
        raw_xml = await RateLimiter.retry_with_backoff(
            lambda: self._get("efetch.fcgi", fetch_params)
        )
        return _parse_mesh_xml(raw_xml, term)


def _parse_pubmed_xml(xml_text: str) -> list[Article]:
    """Parse PubMed efetch XML into Article objects."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    articles = []
    for record in root.findall(".//PubmedArticle"):
        article = _parse_single_pubmed_record(record)
        if article:
            articles.append(article)
    return articles


def _parse_single_pubmed_record(record: Element) -> Optional[Article]:
    try:
        citation = record.find("MedlineCitation")
        if citation is None:
            return None

        pmid_el = citation.find("PMID")
        pmid = pmid_el.text.strip() if pmid_el is not None else None

        article_el = citation.find("Article")
        if article_el is None:
            return None

        title_el = article_el.find("ArticleTitle")
        title = _strip_xml_text(title_el) if title_el is not None else ""
        if not title:
            return None

        # Abstract (may have multiple AbstractText elements)
        abstract_parts = []
        for ab in article_el.findall(".//AbstractText"):
            label = ab.get("Label", "")
            text = _strip_xml_text(ab) or ""
            if label:
                abstract_parts.append(f"{label}: {text}")
            elif text:
                abstract_parts.append(text)
        abstract = " ".join(abstract_parts) if abstract_parts else None

        # Authors
        authors = []
        for author in citation.findall(".//Author"):
            last = author.findtext("LastName", "")
            first = author.findtext("ForeName", "")
            if last:
                authors.append(f"{last} {first}".strip())

        # Journal + year
        journal_el = article_el.find("Journal")
        journal = None
        year = None
        if journal_el is not None:
            journal = journal_el.findtext("Title") or journal_el.findtext(
                "ISOAbbreviation"
            )
            pub_date = journal_el.find(".//PubDate")
            if pub_date is not None:
                year_text = pub_date.findtext("Year") or pub_date.findtext(
                    "MedlineDate", ""
                )
                if year_text:
                    m = re.search(r"\d{4}", year_text)
                    if m:
                        year = int(m.group())

        # Volume / Issue / Pages
        volume = article_el.findtext(".//Volume")
        issue = article_el.findtext(".//Issue")
        pages = article_el.findtext(".//MedlinePgn")

        # DOI
        doi = None
        for loc in record.findall(".//ArticleId"):
            if loc.get("IdType") == "doi":
                doi = loc.text.strip() if loc.text else None
                break

        # MeSH terms
        mesh_terms = []
        for mh in citation.findall(".//MeshHeading"):
            desc = mh.findtext("DescriptorName")
            if desc:
                mesh_terms.append(desc)

        return Article(
            pmid=pmid,
            doi=doi,
            title=title,
            abstract=abstract,
            authors=authors,
            year=year,
            journal=journal,
            volume=volume,
            issue=issue,
            pages=pages,
            mesh_terms=mesh_terms,
            source_dbs=["pubmed"],
        )

    except Exception:
        return None


def _strip_xml_text(el: Element) -> str:
    """Get text content of an element, stripping child tags (e.g. <b>, <i>)."""
    return "".join(el.itertext()).strip()


def _parse_mesh_xml(xml_text: str, term: str) -> dict:
    """Parse MeSH efetch XML into a structured dict."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"term": term, "found": False}

    result: dict = {"term": term, "found": True}

    # Preferred name
    name_el = root.find(".//DescriptorName")
    result["preferred_name"] = name_el.text.strip() if name_el is not None else term

    # Entry terms (synonyms)
    result["entry_terms"] = [
        el.text.strip()
        for el in root.findall(".//EntryTerm")
        if el.text
    ]

    # Tree numbers
    result["tree_numbers"] = [
        el.text.strip()
        for el in root.findall(".//TreeNumber")
        if el.text
    ]

    # Year introduced
    year_el = root.find(".//DateCreated/Year")
    result["year_introduced"] = int(year_el.text) if year_el is not None and year_el.text else None

    # Scope note
    scope_el = root.find(".//ScopeNote")
    result["scope_note"] = scope_el.text.strip() if scope_el is not None and scope_el.text else None

    # Allowable qualifiers
    result["qualifiers"] = [
        el.text.strip()
        for el in root.findall(".//QualifierName")
        if el.text
    ]

    return result


# CLI: python -m src.integrations.pubmed_client <pmid>
if __name__ == "__main__":
    import sys

    async def _main():
        pmid = sys.argv[1] if len(sys.argv) > 1 else "28017433"
        import os
        client = PubMedClient(api_key=os.environ.get("NCBI_API_KEY", ""))
        articles = await client.fetch_abstracts([pmid])
        if articles:
            a = articles[0]
            print(f"PMID:    {a.pmid}")
            print(f"Title:   {a.title}")
            print(f"Authors: {', '.join(a.authors[:3])}...")
            print(f"Journal: {a.journal} ({a.year})")
            print(f"DOI:     {a.doi}")
            print(f"MeSH:    {a.mesh_terms[:5]}")
            print(f"Abstract preview: {(a.abstract or '')[:200]}...")
        else:
            print("No results returned.")

    asyncio.run(_main())
