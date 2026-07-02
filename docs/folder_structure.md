# Lit Review Agent — Folder & File Structure
_Last updated: May 2026_

---

## Full Directory Tree

```
Healthcare/research/lit-review-agent/
│
├── README.md                           # User guide: how to run the agent
├── CLAUDE.md                           # Claude Code session context (loaded fresh each session)
│
├── docs/                               # Technical reference documentation
│   ├── architecture.md                 # Full system design
│   ├── folder_structure.md             # This file
│   ├── api_guide.md                    # API capabilities, rate limits, integration specs
│   ├── evidence_reference.md           # Evidence levels, appraisal instruments, PRISMA
│   ├── performance_reference.md        # Token/API benchmarks and session duration estimates
│   └── review_types.md                 # Review type decision guide + physician advisor mapping
│
├── building/                           # Build history and planning artifacts
│   ├── BUILD_STATUS.md                 # Phase completion tracker — update at end of every phase
│   ├── CONVERSION_PLAN.md              # API-to-subscription conversion plan (C0–C6, complete)
│   ├── KNOWN_ISSUES.md                 # Historical bug/improvement log (all fixed)
│   ├── RUNLOG.md                       # Session notes archive
│   ├── roadmap.md                      # Original phased build plan (Phases 0–6)
│   ├── phases_d1_d3.md                 # D1–D3 improvement phase details
│   ├── pdf_downloader_plan.md          # Stage 9.5 implementation plan (implemented)
│   └── pdf_retrieval_improvements.md  # PDF retrieval strategy (implemented)
│
├── src/                                # Python backend
│   │
│   ├── agent/
│   │   ├── __init__.py
│   │   └── state.py                    # Load/save/update state.json; --scan CLI for session resume
│   │
│   ├── skills/                         # One module per skill; defined inputs, defined outputs
│   │   ├── __init__.py
│   │   ├── _base.py                    # Shared prepare_output() / error_output() / parse_save_input()
│   │   ├── review_type_classifier.py   # Stage 1
│   │   ├── pico_refiner.py             # Stage 2 — interactive; Fundamental Four + PICO
│   │   ├── protocol_generator.py       # Stage 3 — PROSPERO-style protocol
│   │   ├── query_builder.py            # Stage 4 — MeSH + free-text; stepwise #n history
│   │   ├── multi_source_retriever.py   # Stage 5 — PubMed, OpenAlex, S2, CrossRef, EuropePMC
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
│   │   └── output_formatter.py         # Stage 16b — 9 output artifacts; --prepare / --save
│   │
│   ├── integrations/                   # One module per external API; rate limiting built in
│   │   ├── __init__.py
│   │   ├── config.py                   # Load API keys from config/api_keys.yaml or env vars
│   │   ├── pubmed_client.py            # E-utilities: esearch, efetch, elink, MeSH DB
│   │   ├── openalex_client.py          # Search, works, OA status, topics, citation counts
│   │   ├── crossref_client.py          # DOI resolution, reference lists (polite pool)
│   │   ├── semantic_scholar_client.py  # Batch paper lookup, citations, TLDRs
│   │   └── europepmc_client.py         # Full-text XML, preprints, grant cross-links
│   │
│   ├── models/                         # Pydantic data models (single source of truth for schemas)
│   │   ├── __init__.py
│   │   ├── article.py                  # Full article schema with all enrichment fields
│   │   ├── pico.py                     # PICO(S) + criteria + Fundamental Four
│   │   ├── query.py                    # Query step with #n history
│   │   └── review.py                   # Full review state (mirrors state.json schema)
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logger.py                   # Structured logging → search_log.md; PRISMA counter mgmt
│       ├── rate_limiter.py             # Token-bucket per API host; retry-with-backoff on 429
│       └── filename_utils.py           # Filename normalization for PDF downloads
│
├── skills/                             # Claude Code skill markdown files (source; deploy to ~/.claude/skills/)
│   ├── lit-review.md                   # Main skill: full systematic review (16 stages)
│   ├── lit-review-quick.md             # Fast-path: scoping review (skip full-text + quality appraisal)
│   └── lit-review-update.md            # Update an existing completed review with new literature
│   #
│   # DEPLOYMENT: copy to ~/.claude/skills/ for global Claude Code access
│   # cp skills/lit-review.md ~/.claude/skills/
│
├── reviews/                            # All completed and in-progress reviews (committed; not PHI)
│   └── {review-slug}/                  # Example: chf-care-transitions-readmission-2026-04/
│       ├── state.json                  # Live state + audit trail; updated after every stage
│       ├── protocol.md                 # Pre-registered protocol (locked before search)
│       ├── search_log.md               # Full audit trail: queries, hit counts, timestamps
│       ├── articles_raw.json           # All retrieved articles (pre-dedup)
│       ├── articles_deduped.json       # After deduplication
│       ├── articles_screened.json      # After abstract screening (include/exclude/borderline)
│       ├── articles_included.json      # Final included set with all enrichment data
│       ├── evidence_table.csv          # Structured data extraction
│       ├── retrieval_list.csv          # Hospital library PDF request list
│       ├── library_request.csv         # PDFs not found automatically (for manual retrieval)
│       ├── download_report.json        # Download status per article
│       ├── quality_report.md           # Per-article quality appraisal
│       ├── synthesis.json              # Synthesis work (evidence map, by-outcome narratives)
│       ├── executive_summary.md        # Physician-advisor grade narrative summary
│       ├── prisma_flow.md              # PRISMA 2020 flow diagram with counts
│       ├── gap_analysis.md             # Evidence gaps + future research directions
│       ├── gap_analysis.json           # Structured gap suggestions (for iteration)
│       ├── citations.ris               # Zotero-importable citation file
│       ├── citations.bib               # BibTeX for academic writing
│       └── pdfs/                       # Downloaded open-access PDFs
│
├── config/
│   ├── settings.yaml                   # Default parameters (committed)
│   ├── api_keys.yaml                   # API credentials — .gitignored; never commit
│   └── api_keys.yaml.template          # Template for api_keys.yaml
│
├── building/
│   ├── BUILD_STATUS.md                 # Active phase + history
│   ├── KNOWN_ISSUES.md                 # Bug log
│   └── PLAN_*.md                       # Phase plans
│
├── tests/                              # 633 unit tests (0 xfail)
│   ├── test_models.py
│   ├── test_state.py
│   ├── test_rate_limiter.py
│   ├── test_api_connectivity.py        # Live API smoke tests — run separately
│   ├── test_skills_phase2.py           # Retrieval, aggregation, dedup
│   ├── test_skills_phase3.py           # Screening, retrieval list, full-text
│   ├── test_skills_phase4.py           # Enrichment, quality, citation chasing
│   ├── test_skills_phase5.py           # Synthesis, output
│   ├── test_skills_phase6.py           # Gap analysis
│   ├── test_skills_phase_c0.py         # Base utilities, config
│   ├── test_skills_phase_c1.py         # Question formulation (--prepare / --save)
│   ├── test_skills_phase_c2.py         # Abstract screening with batching
│   ├── test_skills_phase_c3.py         # Full-text & extraction
│   ├── test_skills_phase_c4.py         # Quality, citation chasing, gap analysis
│   ├── test_skills_phase_c5.py         # Synthesis & output
│   ├── test_skills_phase_d1.py         # Batching for data extractor & quality appraiser
│   ├── test_skills_phase_d2.py         # Evidence map, surrogate flag, completeness check
│   ├── test_skills_phase_d3.py         # Search log, AI disclosure, PRISMA hit counts
│   └── test_skills_pdf_downloader.py   # PDF download functionality
│
├── source_docs/                        # Academic papers that informed the methodology
│   ├── Litsearch_article.pdf           # Considine et al. 2017 — PICO, search strategy, PRISMA
│   ├── litreview_bookchapter.pdf       # Paré & Kitsiou 2015 — review typology, 6-step method
│   └── litreview_germanarticle.pdf     # Blümle et al. 2019 — PubMed MeSH + free-text strategy
│
├── .gitignore
├── requirements.txt
└── pytest.ini
```

---

## Key File Descriptions

### `state.json` (per review)
The authoritative source of truth for a review. Every session begins by reading this file. Comprehensive enough that conversation history is never needed to resume. Updated atomically after every stage. See `architecture.md` Section 7 for the full schema.

### `README.md`
User-facing guide: how to invoke the skills, which skill to use, what to expect, session resume, output files, and API key setup. Start here if you are new to the agent.

### `CLAUDE.md` (project root)
Provides complete project context for a fresh Claude Code session. Covers: what this project does, what has been built, how the skill works, where to find things, and the end-of-phase development checklist. Must be maintained as the project evolves.

### `building/BUILD_STATUS.md`
Tracks which implementation phases are complete and what to work on next. Enables between-session continuity during development without rereading the full roadmap. Updated at end of every build phase.

### `skills/lit-review.md` → `~/.claude/skills/lit-review.md`
The Claude Code skill file that orchestrates the entire workflow conversationally. Contains session-routing logic (loads `state.json`, routes to current stage), checkpoint text, and references to Python scripts called via Bash. Deploy to `~/.claude/skills/` to make it accessible from any Claude Code context.

### `config/settings.yaml`
```yaml
defaults:
  screening_mode: thorough        # thorough or fast
  max_iterations: 3
  batch_size_thorough: 20         # abstracts per LLM call (thorough mode)
  batch_size_fast: 50             # abstracts per LLM call (fast mode)
  default_sources:
    - pubmed
    - openalex
    - semantic_scholar
  default_year_range: 10          # years back from today
  min_relevance_threshold: 0.7
  language_filter: english        # null for no restriction (SR standard)
  species_filter: humans          # null for no restriction

apis:
  pubmed:
    base_url: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/
    rate_limit: 10                # req/s with NCBI key
  openalex:
    base_url: https://api.openalex.org/
    rate_limit: 10
  crossref:
    base_url: https://api.crossref.org/
    rate_limit: 10                # polite pool
  semantic_scholar:
    base_url: https://api.semanticscholar.org/graph/v1/
    rate_limit: 1                 # authenticated; 5 req/s batch endpoint
  europepmc:
    base_url: https://www.ebi.ac.uk/europepmc/webservices/rest/
    rate_limit: 10
```

### `config/api_keys.yaml` (.gitignored)
```yaml
ncbi_api_key: ""
openalex_email: ""
crossref_mailto: ""
unpaywall_email: ""
semantic_scholar_api_key: ""    # Optional; blank = unauthenticated
```

---

## Review Slug Convention

Reviews are named: `{topic-keywords}-{YYYY-MM}` using kebab-case.

Examples:
- `chf-readmission-interventions-2026-04`
- `interqual-vs-mcg-criteria-2026-05`
- `icu-early-mobility-los-2026-06`

The slug becomes the folder name under `reviews/` and the `review_id` in `state.json`.

---

## Notes

- `reviews/` is committed to the repo. Review outputs are research summaries, not PHI-sensitive.
- `api_keys.yaml` is `.gitignored`. Use environment variables for any CI/CD use.
- `skills/*.md` files are the source. Deploy to `~/.claude/skills/` manually for global Claude Code access.
- `building/` contains development history only — not needed for running reviews.
- `source_docs/` contains the three academic PDFs that informed this architecture. Keep for reference.
