# Lit Review Agent — Folder & File Structure
_Last updated: July 2026_

---

## Full Directory Tree

```
lit-review-agent/
│
├── README.md                           # User guide: how to run the agent
├── CLAUDE.md                           # Claude Code session context (loaded fresh each session)
├── CONTRIBUTING.md                     # Setup, test, and pull request instructions
├── CODE_OF_CONDUCT.md                  # Community expectations
├── CITATION.cff                        # Citation metadata (GitHub "Cite this repository")
├── LICENSE                             # MIT
├── Makefile                            # `make deploy` (install skills), `make package-deps` (Pandoc/xelatex)
├── requirements.txt                    # Python dependencies
├── pytest.ini                          # pytest configuration
│
├── docs/                               # Technical reference documentation
│   ├── architecture.md                 # Full system design
│   ├── folder_structure.md             # This file
│   ├── api_guide.md                    # API capabilities, rate limits, integration specs
│   ├── evidence_reference.md           # Evidence levels, appraisal instruments, PRISMA
│   ├── performance_reference.md        # Token/API benchmarks and session duration estimates
│   └── review_types.md                 # Review type decision guide
│
├── src/                                # Python backend — pure data layer, no LLM calls
│   │
│   ├── agent/
│   │   └── state.py                    # Load/save/update state.json; --scan CLI for session resume
│   │
│   ├── skills/                         # One module per pipeline stage; defined inputs, defined outputs
│   │   ├── _base.py                    # Shared prepare_output() / error_output() / parse_save_input()
│   │   ├── _keywords.py                # Shared stopwords/synonym/term-extraction utilities
│   │   ├── _render.py                  # Pandoc subprocess wrapper (PDF + DOCX rendering)
│   │   ├── _study_design.py            # Shared study-design controlled vocabulary + canonicalizer
│   │   ├── review_type_classifier.py   # Stage 1
│   │   ├── pico_refiner.py             # Stage 2 — interactive; Fundamental Four + PICO
│   │   ├── protocol_generator.py       # Stage 3 — PROSPERO-style protocol
│   │   ├── query_builder.py            # Stage 4 — MeSH + free-text; stepwise #n history
│   │   ├── multi_source_retriever.py   # Stage 5 — PubMed, OpenAlex, S2 (Europe PMC opt-in)
│   │   ├── aggregator.py               # Stage 6 — normalize + tag sources
│   │   ├── deduplicator.py             # Stage 7 — DOI → title sim → author/year fallback
│   │   ├── abstract_screener.py        # Stage 8 — dual-pass LLM; --prepare / --save; batched
│   │   ├── citation_chaser.py          # Stage 8.5 — backward (CrossRef) + forward (S2); appends includes to screened set
│   │   ├── retrieval_list_generator.py # Stage 9 — combined CSV (database + citation-chased)
│   │   ├── pdf_downloader.py           # Stages 9.5 / 9.6 — OA waterfall (Unpaywall + PMC),
│   │   │                               #   --import-from-downloads for manual browser downloads
│   │   │                               #   (Stage 9.6), --reconcile-disk
│   │   ├── fulltext_assessor.py        # Stage 10 — PDF eligibility assessment; --prepare / --save
│   │   ├── data_extractor.py           # Stage 11 — structured extraction → evidence_table.csv
│   │   ├── enricher.py                 # Stage 12 — citation counts, TLDRs, OA status
│   │   ├── quality_appraiser.py        # Stage 13 — AMSTAR-2 / RoB2 / STROBE / COREQ / SANRA / Expert Opinion
│   │   ├── evidence_classifier.py      # Stage 14 — NHMRC levels I–IV (rule-based)
│   │   ├── gap_analyzer.py             # Stage 15 — gaps, suggested queries, iterate decision
│   │   ├── synthesizer.py              # Stage 16a — narrative synthesis; --prepare / --save
│   │   ├── output_formatter.py         # Stage 16b — output artifacts; --prepare / --save
│   │   ├── iteration_manager.py        # `/lit-review-update` — retrieve, dedup, screen, merge new includes
│   │   ├── packager.py                 # `/lit-review-package` — assembles PDF + DOCX from a completed review
│   │   └── templates/                  # Pandoc render templates (LaTeX + DOCX reference doc)
│   │
│   ├── integrations/                   # One module per external API; rate limiting built in
│   │   ├── config.py                   # Load API keys from config/api_keys.yaml or env vars
│   │   ├── pubmed_client.py            # E-utilities: esearch, efetch, elink, MeSH DB
│   │   ├── openalex_client.py          # Search, works, OA status, topics, citation counts
│   │   ├── crossref_client.py          # DOI resolution, reference lists (polite pool)
│   │   ├── semantic_scholar_client.py  # Batch paper lookup, citations, TLDRs
│   │   └── europepmc_client.py         # Full-text XML, preprints, grant cross-links
│   │
│   ├── models/                         # Pydantic data models (single source of truth for schemas)
│   │   ├── article.py                  # Full article schema with all enrichment fields
│   │   ├── pico.py                     # PICO(S) + criteria + Fundamental Four
│   │   ├── query.py                    # Query step with #n history
│   │   └── review.py                   # Full review state (mirrors state.json schema)
│   │
│   ├── tools/
│   │   └── s2_merge.py                 # One-shot: fetch Semantic Scholar results, merge into articles_raw.json
│   │
│   └── utils/
│       ├── logger.py                   # Structured logging → search_log.md; PRISMA counter mgmt
│       ├── rate_limiter.py             # Token-bucket per API host; retry-with-backoff on 429
│       ├── filename_utils.py           # Filename normalization for PDF downloads
│       └── pdf_utils.py                # Shared PDF validation constants
│
├── skills/                             # Claude Code skill markdown files (source; deploy via `make deploy`)
│   ├── lit-review.md                   # Main skill: full systematic review (16 stages, 6 checkpoints)
│   ├── lit-review-quick.md             # Fast-path: scoping scan (skip full-text + quality appraisal)
│   ├── lit-review-update.md            # Update an existing completed review with new literature
│   └── lit-review-package.md           # Bundle a completed review into a polished PDF + DOCX
│
├── reviews/                            # Completed and in-progress reviews (mostly gitignored)
│   └── demo-chf-care-transitions/      # Worked example, committed for reference
│       ├── state.json                  # Live state + audit trail; updated after every stage
│       ├── protocol.md                 # Pre-registered protocol (locked before search)
│       ├── search_log.md               # Full audit trail: queries, hit counts, timestamps
│       ├── articles_included.json      # Final included set with all enrichment data
│       ├── evidence_table.csv          # Structured data extraction
│       ├── retrieval_list.csv          # Combined candidate list with PDF status
│       ├── library_request.csv         # PDFs not found automatically (for manual retrieval)
│       ├── download_report.json        # Download status per article
│       ├── quality_report.md           # Per-article quality appraisal
│       ├── synthesis.json              # Synthesis work (evidence map, by-outcome narratives)
│       ├── executive_summary.md        # Academic-physician-grade narrative summary
│       ├── prisma_flow.md              # PRISMA 2020 flow diagram with counts
│       ├── gap_analysis.md             # Evidence gaps + future research directions
│       ├── gap_analysis.json           # Structured gap suggestions (for iteration)
│       ├── ai_disclosure.md            # AI-use disclosure written on completion
│       ├── citations.ris               # Zotero-importable citation file
│       └── citations.bib               # BibTeX for academic writing
│       # Note: only articles_included.json ships in the demo; the raw/deduped/
│       # screened intermediate article lists are not committed for this example.
│
├── config/
│   ├── settings.yaml                   # Default parameters (committed)
│   └── api_keys.yaml.template          # Template — copy to api_keys.yaml (gitignored) and fill in
│
└── tests/                              # Unit tests
    ├── test_models.py
    ├── test_state.py
    ├── test_rate_limiter.py
    ├── test_api_connectivity.py        # Live API smoke tests — run separately
    └── ...                             # One or more test files per skill/integration module
```

---

## Key File Descriptions

### `state.json` (per review)
The authoritative source of truth for a review. Every session begins by reading this file. Comprehensive enough that conversation history is never needed to resume. Updated atomically after every stage. See `architecture.md` Section 7 for the full schema.

### `README.md`
User-facing guide: how to invoke the skills, which skill to use, what to expect, session resume, output files, and API key setup. Start here if you are new to the agent.

### `CLAUDE.md` (project root)
Provides project context for a fresh Claude Code session in this repo: what the project does, how the skill works, and where to find things.

### `skills/lit-review.md` → `~/.claude/skills/lit-review/SKILL.md`
The Claude Code skill file that orchestrates the entire workflow conversationally. Contains session-routing logic (loads `state.json`, routes to current stage), checkpoint text, and references to Python scripts called via Bash.

**Deploying skills:** Claude Code discovers skills from `~/.claude/skills/<name>/SKILL.md` — a *folder* per skill, not a flat file. A plain `cp skills/lit-review.md ~/.claude/skills/` will not work. Always use:

```bash
make deploy
```

which copies each of the four skill files into its own `~/.claude/skills/{name}/SKILL.md`. Re-run it any time you pull skill changes.

### `config/settings.yaml`
Reference only: not loaded by any code. Effective defaults are constants in `src/` (e.g. `multi_source_retriever.py`, `models/review.py`); `max_iterations` is stored per review in `state.json`, and `screening_mode` is a stored label that does not change batching (the batch size is 50 in all stage modules).

```yaml
max_iterations: 3
batch_size: 20                  # historical; actual batch size is 50 (BATCH_SIZE_DEFAULT)
batch_size_fast: 50             # historical
default_sources:
  - pubmed
  - openalex
  - semantic_scholar
default_year_range: 10          # years back from today
default_screening_mode: thorough
default_max_results_per_source: 500
```

### `config/api_keys.yaml.template` → `config/api_keys.yaml` (.gitignored)
```yaml
ncbi_api_key: ""
openalex_email: ""
crossref_mailto: ""
unpaywall_email: ""
semantic_scholar_api_key: ""    # Recommended; blank = unauthenticated pool (often 429-limited)
report_author: ""               # Your name on generated reports; blank = "Prepared using lit-review-agent"
```

---

## Review Slug Convention

Reviews are named: `{topic-keywords}-{YYYY-MM}` using kebab-case.

Examples:
- `chf-readmission-interventions-2026-04`
- `icu-early-mobility-los-2026-06`

The slug becomes the folder name under `reviews/` and the `review_id` in `state.json`.

---

## Notes

- `reviews/` is gitignored except for the `demo-*` prefix — only the worked example is committed; your own reviews stay local.
- `config/api_keys.yaml` is gitignored. Use environment variables instead for any CI/CD use.
- `skills/*.md` files are the source. Deploy with `make deploy` — do not hand-copy them into `~/.claude/skills/`.
- `RUNLOG.md`, written at the repo root on review completion, is a local run artifact and is gitignored.
