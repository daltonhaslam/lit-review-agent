# API Integration Guide
_Last updated: April 2026_

---

## Quick Reference

| API | Free? | Auth Required | Rate Limit | Best Unique Data |
|---|---|---|---|---|
| PubMed E-utilities | Yes | NCBI key (optional; get one) | 3/s no key → 10/s with key | MeSH controlled vocabulary; biomedical depth; PMID linkage |
| OpenAlex | Yes (freemium) | Email key (optional but recommended) | Generous free tier (~$1/day credit) | Topic taxonomy; institution/author metadata; full citation graph |
| CrossRef | Yes | `mailto=` polite pool | 10/s polite | Canonical DOI metadata; reference lists; funder data |
| Semantic Scholar | Yes | Optional key (hard to get new ones) | Unauthenticated pool; 500 papers/batch | TLDRs; SPECTER2 embeddings; influential-citation flag |
| Unpaywall | Yes | Email required (not a key) | 100k/day | Best-in-class OA PDF URL resolution |
| Europe PMC | Yes | None | 10/s; 500/min | Full-text XML; preprints; grant cross-links |

---

## 1. PubMed E-utilities (NCBI)

### Why Use It
PubMed is the primary source for biomedical literature. It covers 36M+ citations. MeSH (Medical Subject Headings) controlled vocabulary is unmatched for biomedical precision — no other API provides this.

### Auth
Register free at [NCBI account page](https://www.ncbi.nlm.nih.gov/account/) for an API key. Without key: 3 req/s. With key: 10 req/s. Add key as `api_key=YOUR_KEY` to all requests.

### Key Endpoints

| Endpoint | Purpose | Key Params |
|---|---|---|
| `esearch.fcgi` | Query → PMID list | `db`, `term`, `retmax`, `retstart`, `usehistory=y` |
| `efetch.fcgi` | PMIDs → full records | `db=pubmed`, `id` (comma-separated), `rettype=abstract`, `retmode=xml` |
| `elink.fcgi` | Find related records | `dbfrom=pubmed`, `id`, `cmd=neighbor` |
| `esummary.fcgi` | PMIDs → doc summaries | `db=pubmed`, `id` |
| `esearch.fcgi` (MeSH) | Search MeSH terms | `db=mesh`, `term` |
| `efetch.fcgi` (MeSH) | MeSH term details | `db=mesh`, `id` (MeSH UID) |

### MeSH Lookup Pattern
```python
# Step 1: search MeSH DB for term
response = esearch(db="mesh", term="heart failure")
mesh_uid = response["idlist"][0]

# Step 2: fetch MeSH record
record = efetch(db="mesh", id=mesh_uid)
# record contains: entry_terms (synonyms), tree_number, 
#                  scope_note, year_introduced, allowable_qualifiers
```

### Query Construction (following Blümle et al. 2019)

For each PICO aspect, generate BOTH:

1. **MeSH query:** `"Heart Failure"[Mesh]`
   - Explode = ON by default (includes all narrower terms)
   - Can restrict to Major Topic: `"Heart Failure"[MeSH Major Topic]`
   - Can add subheadings: `"Heart Failure/therapy"[Mesh]`
   - **Warning:** check year introduced — new MeSH terms miss older literature. Use parent term OR free-text fallback.

2. **Free-text query:** `heart failure*[tiab] OR cardiac failure*[tiab] OR congestive heart failure[tiab]`
   - `[tiab]` = title + abstract (better precision than `[tw]` which includes MeSH terms)
   - Truncation with `*` (PubMed, max ~600 endings)
   - OR all synonyms, spelling variants, abbreviations
   - Do NOT rely on Automatic Term Mapping (ATM) alone — it misses domain abbreviations (e.g., "ROP" as retinopathy of prematurity)

3. **Combine aspects with AND:** `(#3 OR #4) AND (#8 OR #9) AND ("Randomized Controlled Trial"[pt] OR rando*[tiab])`

### Filters (with free-text fallbacks)
```
Article type:       "Randomized Controlled Trial"[pt]  +  rando*[tiab]
Humans only:        Species → humans  +  human*[tiab]
Date range:         2014:2026[dp]
Language:           english[la]
```
**Critical:** Filters based on MeSH/article-type only hit indexed records (~90% of PubMed). Always pair with free-text fallback to catch the ~10% unindexed.

### History Feature (`#n` steps)
Use `usehistory=y` + `WebEnv` + `query_key` to save steps in NCBI's history server. Each step gets a `#n` reference. Log all steps in `search_log.md` for reproducibility.

### 2026 Note
PMC E-utilities migration completed February 2026 — verify PMC-specific `efetch` calls still return expected results for full-text retrieval.

---

## 2. OpenAlex

### Why Use It
The most comprehensive open academic database (250M+ works). Best for cross-disciplinary coverage, institution/author metadata, topic taxonomy, and full citation graph. OA status embedded — saves separate Unpaywall call for most records.

### Auth
Create account at [openalex.org](https://openalex.org). Add email as `mailto=` param for polite pool access (equivalent to free tier). API key from settings for tracking.

Free tier: ~$1/day credit — enough for hundreds of searches and thousands of work lookups per day. Bulk snapshot remains completely free.

### Key Endpoints

| Endpoint | Purpose | Example |
|---|---|---|
| `GET /works` | Search + filter works | `/works?search=heart+failure&filter=publication_year:2015-2026` |
| `GET /works/{id}` | Get single work | `/works/W2741809807` |
| `POST /works/batch` | Batch lookup by DOI/PMID | Body: `{"filter": "doi:10.xxxx/xxxxx"}` |

### Useful Filters
```
filter=publication_year:2015-2026
filter=type:article
filter=language:en
filter=open_access.is_oa:true
filter=concepts.id:C71924100   (concept = Medicine)
```

### Response Fields of Interest
- `doi`, `title`, `abstract_inverted_index` (must reconstruct abstract)
- `cited_by_count` — citation count
- `concepts` — hierarchical topic tags with score
- `open_access.is_oa`, `open_access.oa_url` — OA status + URL (from embedded Unpaywall data)
- `referenced_works` — DOIs of references (for backward citation chasing)
- `cited_by_api_url` — endpoint to get papers citing this work

### Abstract Reconstruction
OpenAlex abstracts are stored as inverted indexes (word → position list). Reconstruct with:
```python
def reconstruct_abstract(inverted_index: dict) -> str:
    words = [''] * (max(max(v) for v in inverted_index.values()) + 1)
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    return ' '.join(words)
```

---

## 3. CrossRef REST API

### Why Use It
The canonical DOI registry. Best for resolving DOIs to complete metadata, accessing reference lists for backward citation chasing, and normalizing inconsistent citations.

### Auth
Add `mailto=your@email.com` as query parameter to enter the polite pool (10 req/s vs 5 req/s public).

### Rate Limits (revised December 2025)
- **Public pool:** 5 req/s single-record, 1 req/s list, 1 concurrent
- **Polite pool:** 10 req/s single-record, 3 req/s list, 3 concurrent
- This was the first rate limit change in 12 years. Always use polite pool.

### Key Endpoints

| Endpoint | Purpose | Notes |
|---|---|---|
| `GET /works/{doi}` | Resolve DOI → metadata | Canonical; most complete |
| `GET /works?filter=has-references:true` | Find works with open references | Coverage varies by publisher |
| `GET /works/{doi}/references` | Get references of a work | Only if publisher marked as open |

### Reference List Coverage Caveat
Not all publishers make reference lists open. Use `filter=has-references:true` to find those that do. For works without open references, fall back to Semantic Scholar or Europe PMC.

### Use in Deduplication
CrossRef is the authoritative source for DOI normalization. Use it to resolve variant DOI formats before fuzzy matching.

---

## 4. Semantic Scholar Academic Graph

### Why Use It
Provides data not available elsewhere: AI-generated TLDRs (~50-word summaries), SPECTER2 paper embeddings (for similarity ranking), and influential-citation flag (distinguishes highly-cited from methodologically influential papers). Batch endpoints make enrichment cheap.

### Auth
API keys no longer issued to free-email accounts (tightened in 2025). Design for unauthenticated operation as default. Institutional email holders may apply. If no key: use shared unauthenticated pool (practical rate: ~3 req/s sustained).

### Key Endpoints

| Endpoint | Purpose | Limit |
|---|---|---|
| `POST /paper/batch` | Lookup up to 500 papers by ID | 1 call = 500 papers |
| `GET /paper/search/bulk` | Bulk search | Paginate with `token` |
| `GET /paper/{id}/citations` | Papers citing this paper | For forward citation chasing |
| `GET /paper/{id}/references` | References of this paper | For backward chasing |

### Batch Lookup Pattern
```python
# Always use batch endpoint for enrichment — 1 call = up to 500 papers
response = post("/paper/batch", json={
    "ids": ["PMID:12345678", "DOI:10.1001/jama.2020.1234", ...],
    "fields": "title,year,citationCount,influentialCitationCount,tldr,isOpenAccess,openAccessPdf"
})
```

### Key Fields
- `citationCount` — total citations
- `influentialCitationCount` — papers that cite this in a methodologically significant way
- `tldr.text` — AI-generated 50-word summary (use as quick abstract in screening)
- `isOpenAccess`, `openAccessPdf.url` — OA status
- `referenceCount` — number of references

### Citation Velocity Calculation
```python
years_since_pub = current_year - paper.year
citation_velocity = paper.citation_count / max(years_since_pub, 1)
```

---

## 5. Unpaywall

### Why Use It
The best-in-class source for open-access PDF URLs. After the May 2025 rewrite, responses are ~50ms (vs 500ms previously), making inline per-article calls feasible. If using OpenAlex, its embedded Unpaywall data may be sufficient for most records.

### Auth
Add `email=your@email.com` to all requests. Not a key — just identification for the polite pool.

### Key Endpoint
```
GET https://api.unpaywall.org/v2/{doi}?email=your@email.com
```

### Key Response Fields
```json
{
  "is_oa": true,
  "best_oa_location": {
    "url": "https://europepmc.org/articles/PMC...",
    "url_for_pdf": "https://europepmc.org/articles/PMC.../pdf",
    "license": "cc-by",
    "version": "publishedVersion",
    "host_type": "repository"
  }
}
```

### When to Call
- After abstract screening, for the include + borderline list
- Fills `retrieval_list.csv` with direct OA PDF URLs
- Skip if OpenAlex `open_access.oa_url` is sufficiently fresh for the use case

---

## 6. Europe PMC

### Why Use It
Only API providing full-text XML for millions of articles (primarily PMC-hosted). No auth required. Also the best coverage of preprints (bioRxiv, medRxiv via Europe PMC Preprints) and European grant cross-links.

### Auth
None. Set a descriptive `User-Agent` header as good practice.

### Key Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /search?query=...&resulttype=core` | Search + abstract |
| `GET /fullTextXML/{id}` | Full-text XML (PMC articles) |
| `GET /annotations?articleIds={id}` | Named entity annotations |

### Full-Text Retrieval
```python
# Check if full text available
response = get(f"/article/{pmcid}/fullTextXML")
# Returns structured XML: article metadata, body sections, references, figures
```

Use full-text for:
- Full-text eligibility assessment (if abstract is ambiguous)
- Data extraction (methods, results sections)
- Reference lists (backward citation chasing for non-CrossRef-open articles)

---

## 7. Rate Limiting Strategy

All clients share a centralized `RateLimiter` in `src/utils/rate_limiter.py` using a per-host token bucket.

```python
class RateLimiter:
    def __init__(self, rate: float, burst: int = 1):
        # rate = tokens/second
        # Shared instance per (host, endpoint_class)
    
    async def acquire(self):
        # Blocks until a token is available
    
    @staticmethod
    async def retry_with_backoff(func, max_retries=3):
        # On 429: exponential backoff (1s, 2s, 4s)
        # On 503: retry after Retry-After header
```

### Per-Host Budgets
```python
limiters = {
    "eutils.ncbi.nlm.nih.gov": RateLimiter(rate=9.0),     # 10/s; buffer 1
    "api.openalex.org": RateLimiter(rate=8.0),
    "api.crossref.org": RateLimiter(rate=9.0),             # polite pool
    "api.semanticscholar.org": RateLimiter(rate=0.9),      # 1/s guaranteed
    "api.unpaywall.org": RateLimiter(rate=5.0),            # generous
    "www.ebi.ac.uk": RateLimiter(rate=9.0),
}
```

---

## 8. Recommended API Call Sequence Per Stage

| Stage | APIs Called | Pattern |
|---|---|---|
| Stage 4 (Query build) | PubMed (MeSH lookup) | Serial; 1 call per PICO aspect |
| Stage 5 (Retrieval) | PubMed, OpenAlex, Semantic Scholar | Parallel async; batch efetch for PubMed abstracts |
| Stage 6 (Aggregation) | CrossRef (DOI resolution for missing DOIs) | Batch; on-demand |
| Stage 12 (Enrichment) | Semantic Scholar (batch 500), OpenAlex (batch), Unpaywall | Parallel; batch-first |
| Stage 8.5 (Citation chasing) | CrossRef (references), Semantic Scholar (citations), OpenAlex (cited_by) | Per included article; serial to manage rate limits |

---

## 9. Environment Setup

```bash
# Required (get free API keys)
export NCBI_API_KEY="your_ncbi_key"
export OPENALEX_EMAIL="your@email.com"
export CROSSREF_MAILTO="your@email.com"
export UNPAYWALL_EMAIL="your@email.com"

# Optional (Semantic Scholar key; hard to obtain without institutional affiliation)
export S2_API_KEY="your_s2_key"
```

Or store in `config/api_keys.yaml` (not committed):
```yaml
ncbi_api_key: "your_ncbi_key"
openalex_email: "your@email.com"
crossref_mailto: "your@email.com"
unpaywall_email: "your@email.com"
semantic_scholar_api_key: ""
```

`src/integrations/__init__.py` loads from yaml with env var override.
