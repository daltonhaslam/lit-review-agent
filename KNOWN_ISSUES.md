# Known Issues

Current limitations, stated plainly. To report something new, open a
[GitHub issue](https://github.com/daltonhaslam/lit-review-agent/issues).

_Last reviewed: 2026-10-17_

## By design

- **Open-access-only retrieval.** Stage 9.5 downloads only legally open copies
  (Unpaywall, OpenAlex, PubMed Central, Europe PMC, Crossref). Anything else goes
  to a guided manual step or `library_request.csv`. There is no paywall bypass or
  institutional-proxy integration, and there won't be.
- **macOS assumptions.** The manual browser-retrieval step (Stage 9.6) and
  `make package-deps` assume macOS (`open -a "Google Chrome"`, `~/Downloads`,
  Homebrew).

## Retrieval

- **Semantic Scholar without a key.** The shared unauthenticated pool often
  answers HTTP 429 for long stretches. Retrieval retries with backoff and then
  reports Semantic Scholar as a *degraded source* in the run summary,
  `search_log.md`, and the packaged report's Methods section. It is never
  recorded as a silent zero. A free key from
  https://www.semanticscholar.org/product/api, set as `semantic_scholar_api_key` in
  `config/api_keys.yaml` (or `SEMANTIC_SCHOLAR_API_KEY`), fixes this.
- **Best-effort stages swallow outages.** Citation chasing (Stage 8.5),
  enrichment (Stage 12), and PDF download (Stage 9.5) treat network failures as
  "nothing found". A total outage during citation chasing looks the same as
  "no new references". The skill asks you to confirm connectivity when the
  chase returns zero.
- **PubMed partial pages.** If PubMed fails mid-pagination, abstracts already
  fetched in that run are dropped. They are not kept as a partial result.
- **No per-source timeout.** Retrieval has no overall deadline per source. A hung
  source holds up the whole Stage 5 gather.
- **Updates and iterations search PubMed only.** `/lit-review-update` and the
  Checkpoint 6 iteration loop re-query PubMed. They do not re-query OpenAlex or
  Semantic Scholar.
- **Year range doesn't reach the screener.** `--year-from/--year-to` filter the
  API calls but never set `date_range` in `state.json`, so Stage 8's date
  pre-filter never runs.

## Classification and labels

- **NHMRC mapping differs from the NHMRC 2009 table.** Retrospective cohort and
  case-control studies map to III-3, not III-2. Interrupted time series and
  controlled before-after studies map to III-1. See "How the classifier maps
  study designs" in [`docs/evidence_reference.md`](docs/evidence_reference.md).
  This is pending maintainer review.
- **`review_type` and `screening_mode` are labels.** Both are stored and shape the
  Claude-written protocol, but no pipeline stage changes its behavior based on
  them. Every review type runs the same stages and the same quality appraisal.
- **`config/settings.yaml` is not loaded.** Effective defaults are constants in
  `src/`.
- **GRADE is not implemented.** It appears in the docs as reference methodology
  only.

## Quick path (`/lit-review-quick`)

- `prisma_counts.identified` is the post-deduplication count. On the quick path,
  `prisma_flow.md`'s duplicates-removed and full-text lines are not meaningful.
- `ai_disclosure.md` describes the full pipeline and needs hand-editing for a
  quick review.
- `/lit-review-package` does not support quick reviews. The packager's
  quick-review detector checks for `review_type == narrative`, but quick reviews
  are saved as `scoping`. Validation therefore fails on missing source files
  rather than giving a clear message.

## Tests and environment

- **Network tests.** `tests/test_api_connectivity.py` makes live calls and is
  skipped unless `LIT_REVIEW_NETWORK_TESTS=1`. Every other test runs offline:
  `tests/conftest.py` blocks non-loopback network access.
- **Python version.** The test suite passes on Python 3.11 (primary) and on macOS's
  system Python 3.9. Other versions are untested.
- **Dependency advisories.** `requirements.txt` caps `pypdf<6` and `lxml<6`. The
  newest allowed versions (pypdf 5.9, lxml 5.4) carry published advisories that
  are fixed only in the next major versions. pypdf parses downloaded PDFs. lxml
  is not imported by the code. The test suite passes on pypdf 6, lxml 6, and
  pytest 9, but the major bump hasn't been made yet.
