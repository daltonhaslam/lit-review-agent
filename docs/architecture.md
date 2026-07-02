# Lit Review Agent — System Architecture
_Last updated: May 2026_

---

## 1. System Overview

The Lit Review Agent is a Claude Code skill that replicates the rigor of a professional academic librarian conducting a systematic literature review. It consists of three layers:

- **Skill layer** (`~/.claude/skills/lit-review.md`) — orchestrates the conversational workflow; loaded when user invokes `/lit-review`
- **Python backend** (`src/`) — pure data layer; handles API calls, file I/O, and data formatting. No LLM calls — no Anthropic SDK dependency.
- **File-based state** (`reviews/{slug}/state.json`) — authoritative source of truth; enables session resumption without conversation history

**Interaction model:**
1. User types `/lit-review` in Claude Code
2. Agent reads `state.json` from active reviews; offers to resume or start new
3. For each LLM stage: Python `--prepare` formats data → Claude in conversation performs the judgment task → Python `--save` writes output to disk
4. For automated stages: Python runs the full stage (API calls, file I/O) with no Claude turn
5. Outputs are written to `reviews/{slug}/` after every stage
6. At each human checkpoint, Claude tells the user it is safe to clear context and resume later

**LLM model:** All LLM work runs as Claude in the Claude Code conversation (covered by Claude.ai Max subscription). No `ANTHROPIC_API_KEY` required. No API tier assignments — the same model handles all judgment tasks.

---

## 2. Review Type Classification (First-Class Architectural Concept)

Review type is determined **before** query generation because it controls search coverage strategy, appraisal instruments, and synthesis method. Drawn from Paré & Kitsiou (2015, Handbook of eHealth Evaluation).

| Review Type | Coverage Strategy | Appraisal Required | Synthesis Method |
|---|---|---|---|
| **Narrative** | Selective; author-chosen | None | Thematic or chronological |
| **Scoping** | Representative → comprehensive; iterative | None | Analytic frameworks + frequency tables |
| **Systematic** | Exhaustive; a priori protocol; no language restriction | AMSTAR/RoB/GRADE | Meta-analysis or qualitative synthesis |
| **Umbrella** | Exhaustive for SRs only | AMSTAR-2 for each SR | Tabular comparison of SR findings |
| **Realist** | Iterative, purposive to theoretical saturation | Mixed instruments | Qualitative (aggregative or interpretive) |
| **Critical** | Representative | None | Interpretivist or positivist |

The `review_type_classifier` skill sets this at Stage 1. It cannot be changed after protocol generation (protocol is locked before search begins, following systematic review standards).

---

## 3. Core Workflow — 16-Stage Pipeline

The process is iterative: gap analysis may loop back to Stage 4 for additional search rounds.

```
┌─────────────────────────────────────────────────────────────────────┐
│ SESSION 1                                                           │
│  Stage 1: Review Type Classification (Haiku 4.5)                   │
│  Stage 2: PICO(S) Formulation (Sonnet 4.6, interactive)            │
│  ━━━━━━━━━━━━━━━ CHECKPOINT 1: PICO Approval ━━━━━━━━━━━━━━━       │
│  Stage 3: Protocol Generation (Sonnet 4.6)                         │
│  ━━━━━━━━━━━━━━━ CHECKPOINT 2: Protocol Approval ━━━━━━━━━━━━━━━   │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│ SESSION 2                                                           │
│  Stage 4: Query Construction — MeSH + free-text (Sonnet 4.6)       │
│  Stage 5: Multi-Source Retrieval (API calls)                        │
│  Stage 6: Aggregation + Normalization (Python)                      │
│  Stage 7: Deduplication (Python; PRISMA: identified)                │
│  [Optional natural break: state saved, session can end]            │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│ SESSION 3                                                           │
│  Stage 8: Abstract Screening — dual-pass LLM, batched               │
│           (PRISMA: screened)                                        │
│  ━━━━━━━━━━━━━━━ CHECKPOINT 3: Borderline Review ━━━━━━━━━━━━━━━   │
│  Stage 8.5: Citation Chasing — backward + forward (API calls)       │
│             → new includes appended to screened set                 │
│  Stage 9: Retrieval List Generation → retrieval_list.csv            │
│           (single combined list: database + citation-chased)        │
│  Stage 9.5: Automated PDF Download (API-only; no LLM tokens)        │
│             → OA PDFs saved to pdfs/; library_request.csv for       │
│               remaining articles; retrieval_list.csv updated with   │
│               pdf_status column                                     │
│  Stage 9.6 (optional): Manual Browser Retrieval                     │
│             → opens remaining library_request.csv URLs as Chrome    │
│               tabs; user downloads natively via their own access;   │
│               agent matches + moves PDFs via --import-from-downloads;│
│               auto-runs --reconcile-disk so retrieval_list.csv      │
│               reflects disk                                         │
│  ━━━━━━━━━━━━━━ CHECKPOINT 4: Library Retrieval Break ━━━━━━━━━━━━  │
└─────────────────────────────────────────────────────────────────────┘
        ↕ NATURAL MULTI-DAY BREAK: hospital library PDF retrieval ↕
┌─────────────────────────────────────────────────────────────────────┐
│ SESSION 4                                                           │
│  Stage 10: Full-Text Eligibility (Sonnet 4.6; PDF uploads)         │
│            (PRISMA: eligibility)                                    │
│  ━━━━━━━━━━━━━━━ CHECKPOINT 5: Included Set Approval ━━━━━━━━━━━━━ │
│  Stage 11: Data Extraction (Sonnet 4.6)                             │
│  Stage 12: Enrichment — citations, TLDRs, OA (API calls)           │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│ SESSION 5                                                           │
│  Stage 13: Quality Appraisal (Sonnet 4.6; instrument per study)    │
│  Stage 14: Evidence Level Classification (Haiku 4.5; NHMRC I–IV)  │
│  Stage 15: Gap Analysis (Sonnet 4.6)                               │
│  ━━━━━━━━━━━━━━━ CHECKPOINT 6: Iteration Decision ━━━━━━━━━━━━━━━  │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│ SESSION 6                                                           │
│  Stage 16 Step 1: Synthesis (Opus 4.7)                             │
│             → natural pause here to review the synthesis draft      │
│  Stage 16 Step 2: Output Package Generation (Opus + Python)        │
│             → 9 output artifacts written to reviews/{slug}/        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Session Management

### Core Principle
`state.json` is the sole source of truth. Claude Code never relies on conversation history. Every session begins by running:

```bash
python src/agent/state.py --scan reviews/
```

If an in-progress review is found:
```
Found in-progress review: chf-readmission-2026-04
Stage: abstract_screening | 89 articles pending user review
Resume this review? [yes / start new]
```

### At Every Human Checkpoint
Before presenting a checkpoint to the user, the agent:
1. Writes complete state to `state.json`
2. Writes all intermediate artifacts to `reviews/{slug}/`
3. Tells the user: *"Your review state has been saved. You can safely clear this conversation and resume with `/lit-review` in a new session."*

### Natural Break Points (approved for session end)
- After Checkpoint 1 (PICO approved)
- After Checkpoint 2 (Protocol approved) — before retrieval
- After Stage 7 (Dedup complete) — before screening
- After Checkpoint 4 (Stages 9.5 → 9.6 complete) — multi-day break common here; library retrieval for any PDFs still not on disk
- After Checkpoint 5 (Included set)
- After Checkpoint 6 (Iteration decision)
- After Stage 16 Step 1 (Synthesis draft, before Step 2 output generation)

---

## 5. Skill Catalog

Each skill is a standalone Python module with defined inputs, defined outputs, and no hidden side effects. Inputs are loaded from `state.json`; outputs are written back to `state.json` and to artifact files.

**LLM-coupled skills** use the `--prepare → Claude in conversation → --save` pattern. **Automated skills** run Python-only (API calls or data processing); no Claude turn required.

| # | Skill Module | Key Inputs | Key Outputs | Type |
|---|---|---|---|---|
| 1 | `review_type_classifier.py` | Raw question | Review type, depth recommendation | LLM-coupled |
| 2 | `pico_refiner.py` | Raw question, review type | Structured PICO(S), criteria, Fundamental Four | LLM-coupled |
| 3 | `protocol_generator.py` | PICO(S), review type, criteria | `protocol.md` (PROSPERO-style) | LLM-coupled |
| 4 | `query_builder.py` | PICO(S) | MeSH queries, free-text queries, stepwise `#n` history | LLM-coupled |
| 5 | `multi_source_retriever.py` | Queries, sources, year range | Raw article list; `search_log.md` with hit counts | Automated |
| 6 | `aggregator.py` | Raw lists | Normalized list with source tags | Automated |
| 7 | `deduplicator.py` | Normalized list | Deduplicated list; dedup report | Automated |
| 8 | `abstract_screener.py` | Deduped list, PICO(S), criteria | Screened lists (include/exclude/borderline) + rationale | LLM-coupled (batched ×20) |
| 8.5 | `citation_chaser.py` | Abstract-screened includes | New candidates (backward refs + forward citations); includes appended to screened set | LLM-coupled (screening only) |
| 9 | `retrieval_list_generator.py` | Include+borderline list (database + citation-chased) | `retrieval_list.csv` (DOI, PMID, journal, OA URL) | Automated |
| 9.5 | `pdf_downloader.py` | `retrieval_list.csv` | `pdfs/*.pdf`; `retrieval_list.csv` updated with `pdf_status`; `library_request.csv` | Automated |
| 10 | `fulltext_assessor.py` | PDF text + PICO(S), criteria | Final included set + eligibility rationale; PDF excerpts persisted onto each Article (`fulltext_excerpt`) for downstream stages | LLM-coupled |
| 11 | `data_extractor.py` | Included list (prefers `fulltext_excerpt` → abstract → tldr), extraction template constrained to a 29-term controlled `study_design` vocabulary | `evidence_table.csv` (design, N, outcomes, results, `data_source` provenance column) + per-field warnings (low conclusions/funding coverage, empty-input articles) | LLM-coupled (batched ×50, parallel slices) |
| 12 | `enricher.py` | Included list | Citation counts, velocity, TLDRs, OA status, topics | Automated |
| 13 | `quality_appraiser.py` | Included list, review type | Quality scores per article (instrument selected by study type) | LLM-coupled (batched ×20) |
| 14 | `evidence_classifier.py` | Included list (with study designs) | NHMRC level I–IV per article | Automated (rule-based) |
| 15 | `gap_analyzer.py` | Included list, PICO(S) | Gaps, suggested queries, iteration recommendation | LLM-coupled |
| 16a | `synthesizer.py` | Included+appraised list, review type | Narrative synthesis, evidence map, quantitative scaffold | LLM-coupled |
| 16b | `output_formatter.py` | All prior outputs | 10 output artifacts (MD, CSV, RIS, BibTeX, PRISMA, AI disclosure) | LLM-coupled + Automated |

---

## 6. Token Efficiency Design

### Abstract Screening (Highest-Volume LLM Step)
User selects screening mode at review start:

- **`thorough`**: batches of 20 abstracts per Claude turn, structured JSON output
- **`fast`**: batches of 50 per turn, borderlines only escalated to a second pass

Both modes output structured JSON responses, not prose, to minimize output tokens.

### Batched LLM Stages
Three stages process articles in batches of 20 to prevent context overload:

- **Stage 8** (abstract screening): 20 articles per turn; Session Health check after every 9 batches (~180 articles)
- **Stage 11** (data extraction): 20 articles per turn; Session Health check after every 5 batches (~100 articles)
- **Stage 13** (quality appraisal): 20 articles per turn; Session Health check after every 5 batches (~100 articles)

### Context Minimization Per Stage
- Synthesis and executive summary load only the included articles, NOT the full screened list (which may be 500+)
- All intermediate lists live in files; Claude sees only the current-stage summary
- Python `--prepare` formats exactly what Claude needs per turn; `--save` handles all file I/O
- Evidence level classification (Stage 14) is fully automated (rule-based) — no Claude turn required

### Session Health
If context grows large mid-stage, pause at the next batch boundary, end the session, and resume with `/lit-review`. State is always saved at batch boundaries — no work is lost.

---

## 7. State Schema

```json
{
  "review_id": "chf-readmission-2026-04",
  "review_type": "systematic",
  "screening_mode": "thorough",
  "status": "abstract_screening",
  "pico": {
    "P": "Hospitalized adult patients with CHF",
    "I": "Care transitions interventions",
    "C": "Usual care",
    "O": ["30-day readmission rate", "ED visits", "mortality"]
  },
  "inclusion_criteria": [],
  "exclusion_criteria": [],
  "protocol_locked_at": "2026-04-22T14:00:00Z",
  "queries": {
    "pubmed": [
      {"step": "#1", "query": "\"Heart Failure\"[Mesh]", "hits": 245000},
      {"step": "#2", "query": "heart failure*[tiab] OR cardiac failure*[tiab]", "hits": 198000},
      {"step": "#3", "query": "#1 OR #2", "hits": 302000}
    ],
    "openalex": [],
    "semantic_scholar": []
  },
  "sources_searched": ["pubmed", "openalex", "semantic_scholar"],
  "prisma_counts": {
    "identified": 0,
    "screened": 0,
    "eligibility": 0,
    "included": 0,
    "citation_chasing_identified": 0,
    "citation_chasing_screened": 0,
    "citation_chasing_added": 0
  },
  "articles_raw_file": "articles_raw.json",
  "articles_deduped_file": "articles_deduped.json",
  "articles_screened_file": "articles_screened.json",
  "articles_included_file": "articles_included.json",
  "iterations": 0,
  "created_at": "2026-04-22T00:00:00Z",
  "updated_at": "2026-04-22T14:00:00Z"
}
```

Note: Article lists are stored as separate JSON files (not embedded in state.json) to keep state.json small and fast to load for session routing.

---

## 8. Human-in-the-Loop Checkpoints

| # | Checkpoint | Trigger | User action | Can end session after? |
|---|---|---|---|---|
| 1 | PICO Approval | After PICO refinement | Review/edit PICO; approve or request changes | Yes |
| 2 | Protocol Approval | After protocol generation | Review full protocol; approve to lock it | Yes |
| 3 | Borderline Screening | After abstract screening | Classify borderline articles (include/exclude) | Yes — and common to wait for library PDFs |
| 4 | Library Retrieval Break | After automated + manual PDF download (Stage 9.5/9.6) | Submit `library_request.csv` to hospital library; place PDFs in `pdfs/` | Yes — multi-day break common |
| 5 | Included Set Approval | After full-text assessment | Confirm or adjust final included set | Yes |
| 6 | Iteration Decision | After gap analysis | Approve additional iterations or stop | Yes |

---

## 9. API Integrations

| API | Primary Use | Auth | Rate Limit | Unique Data |
|---|---|---|---|---|
| PubMed E-utilities | Primary biomedical search + MeSH lookup | NCBI API key (free) | 10 req/s | MeSH controlled vocabulary, PMID linkage |
| OpenAlex | Cross-disciplinary search, topics, OA status | Email key (free) | Generous free tier | Topic taxonomy, institution metadata, citation counts |
| CrossRef | DOI resolution, reference lists for citation chasing | `mailto=` polite pool | 10 req/s polite | Canonical DOI metadata, funder data |
| Semantic Scholar | Citation velocity, influential-citation flag, TLDRs | Optional key; batch endpoints | 500 papers/batch call | TLDRs, SPECTER2 embeddings, influential-citation flag |
| Unpaywall | OA PDF URL per article | Email required | 100k/day (or via OpenAlex) | Best-in-class OA PDF resolution |
| Europe PMC | Full-text XML when needed; preprint coverage | None | 10 req/s | Full-text XML, preprints, grant cross-links |

**MeSH lookup:** via PubMed E-utilities `esearch/efetch` with `db=mesh`. Access MeSH tree, entry terms (synonyms), introduction year, and subheadings.

**Key 2025–2026 changes to be aware of:**
- CrossRef rate limits revised December 2025 (first change in 12 years) — polite pool required
- PMC E-utilities migration February 2026 — monitor for PMC-specific endpoint behavior changes
- Unpaywall ~10× speed improvement (May 2025) — inline calls now feasible
- Semantic Scholar API keys no longer issued to free-email accounts — design for unauthenticated fallback

---

## 10. Output Package

Eleven artifacts written to `reviews/{slug}/` on completion:

| File | Format | Audience / Purpose |
|---|---|---|
| `executive_summary.md` | Markdown | Physician advisor → C-suite; narrative with inline citations |
| `evidence_table.csv` | CSV | Detailed extraction; import into Excel or Zotero |
| `prisma_flow.md` | Markdown | PRISMA 2020 flow data (stage counts + exclusion reasons) |
| `citations.ris` | RIS | Zotero import (included articles only) |
| `citations.bib` | BibTeX | Academic writing |
| `search_log.md` | Markdown | Full search audit trail written at retrieval time; database, query string, hit count per source; peer-reproducible |
| `protocol.md` | Markdown | Pre-registered protocol (locked before search) |
| `gap_analysis.md` | Markdown | Identified evidence gaps; future research directions |
| `ai_disclosure.md` | Markdown | PRISMA-trAIce 2025 AI disclosure: AI-assisted vs automated stages, human checkpoints, limitations |
| `retrieval_list.csv` | CSV | Master article list (all include+borderline); `pdf_status` column (`downloaded` / `not_found` / `skipped`) populated at Stage 9.5 and updated by Stage 9.6 and `pdf_downloader --reconcile-disk` |
| `library_request.csv` | CSV | Hospital library request — not-found PDFs only (title, DOI, PMID); generated by Stage 9.5 and trimmed by Stage 9.6 as PDFs land on disk |

---

## 11. Evidence Hierarchy and Quality Appraisal

### NHMRC Evidence Levels (applied by `evidence_classifier`)

| Level | Study Design |
|---|---|
| I | Systematic review of all relevant RCTs |
| II | At least one properly designed RCT |
| III-1 | Well-designed pseudorandomised trial |
| III-2 | Comparative study with concurrent controls (cohort, case-control, ITS with control) |
| III-3 | Comparative study with historical controls; two+ single-arm studies; ITS without control |
| IV | Case series (post-test or pre/post without control) |

### Quality Appraisal Instruments (applied by `quality_appraiser`)

Selection is automatic based on study design detected in data extraction:

| Study Type | Instrument | Items |
|---|---|---|
| Systematic review | AMSTAR-2 | 16 items (7 critical) |
| RCT | Cochrane RoB 2.0 | 5 domains |
| Evidence outcomes | GRADE | 4 domains (high/moderate/low/very low) |
| Qualitative study | COREQ | 32 items |
| Observational (cohort/case-control) | STROBE | 22 items |
| Narrative review | SANRA | 6 items × 0–2 (score = total/12) |
| Editorial / commentary / expert opinion | Expert Opinion rubric | 5-item transparency rubric × 0–2 (score = total/10) |

### Surrogate Outcome Warning
Following Considine et al. (2017): the agent flags papers that use surrogate outcomes (e.g., BNP levels instead of readmission rates) and notes that the PICO-specified outcomes are the primary target.

---

## 12. Academic Rigor Features

- **Protocol locking:** protocol generated and locked before any search begins; prevents post-hoc research question drift
- **PROSPERO check:** agent searches PROSPERO for existing registered reviews on the same question before generating a new protocol (to avoid duplicate work)
- **Dual-pass screening:** two independent LLM passes; disagreements flagged and logged; disagreement rate tracked
- **Citation chasing:** backward (references in included papers via CrossRef) + forward (papers citing included papers via Semantic Scholar/OpenAlex) — run at Stage 8.5 after abstract screening; new includes appended to screened set before single library retrieval; documented in PRISMA 2020 two-stream flow
- **PRISMA 2020 tracking:** counters updated at each stage; exclusion reasons logged for each excluded article
- **Full-text-only synthesis (PRISMA-compliant default):** the main `by_outcome` narrative, `evidence_map`, `quantitative_scaffold`, and executive summary are built **only** from articles that cleared full-text eligibility (Stage 10) and were quality-appraised (Stage 13). Abstract-only articles are excluded from the appraised evidence base by default. An **opt-in abstract supplement** (Stage 16 Step 1) renders unappraised, abstract-cleared articles in a clearly-labeled appendix outside the main narrative; it never folds into `evidence_map` or any inline citation in the appraised synthesis.
- **Full audit trail:** every search step logged with timestamp, exact query string, database, hit count
- **Stepwise query history:** PubMed search documented as `#n` steps (following the German PubMed guide) for reproducibility

---

## 13. Informed by Academic Sources

This architecture is derived from three academic sources in `source_docs/`:

| Source | Key Contribution |
|---|---|
| Considine et al. (2017) — *International Emergency Nursing* | PICO framework, "Fundamental Four" pre-PICO checklist, NHMRC evidence levels, PRISMA reporting, dual-reviewer principle, citation chasing importance |
| Paré & Kitsiou (2015) — *Handbook of eHealth Evaluation*, Ch. 9 | Review type typology (narrative/scoping/systematic/umbrella/realist/critical), six-step review process, PROSPERO registration, coverage strategies |
| Blümle et al. (2019) — *Der Schmerz* | PubMed MeSH + free-text strategy, stepwise query history, ATM limitations, precision/recall trade-off, filter fallback with free-text |
