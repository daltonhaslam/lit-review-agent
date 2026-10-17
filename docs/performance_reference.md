# Performance Reference

Typical API call counts, LLM token usage, and session durations for a systematic review of ~500 retrieved articles → ~20 included articles. Based on pipeline architecture as of Phase 6.

> **Note:** The per-stage model names (Haiku/Sonnet/Opus) and the dollar-cost figures below are historical pre-subscription estimates, retained only for relative token sizing. Today all LLM work runs in the Claude Code session (subscription; no per-call API cost) and no code selects models. Phases below are groupings, not stage numbers; see `docs/architecture.md` §3 for the session breakdown.

---

## Summary by Phase

| Phase | LLM Calls | Approx Input Tokens | Approx Output Tokens | Session Time |
|---|---|---|---|---|
| Phase 1: Question Formulation | 3–5 | ~3,000 | ~2,000 | 5–10 min |
| Phase 2: Retrieval | 0 | — | — | 2–5 min |
| Phase 3: Screening (thorough) | ~30 | ~200,000 | ~20,000 | 10–20 min |
| Phase 4: Enrichment + Quality | ~25 | ~60,000 | ~15,000 | 10–20 min |
| Phase 5: Synthesis + Output | 2–3 | ~30,000 | ~8,000 | 5–10 min |
| Phase 6 Iteration (per loop) | ~10 | ~40,000 | ~8,000 | 5–10 min |

---

## Phase-by-Phase Breakdown

### Phase 1: Question Formulation

| Stage | Model | Calls | Input tokens | Output tokens |
|---|---|---|---|---|
| Review type classification | Haiku 4.5 | 1 | ~300 | ~200 |
| PICO refinement | Sonnet 4.6 | 1 | ~1,500 | ~800 |
| Protocol generation | Sonnet 4.6 | 1 | ~2,000 | ~1,500 |
| Query building (MeSH + query gen) | Sonnet 4.6 | 1–2 | ~1,500 | ~600 |

**PubMed API calls:** 4–8 (MeSH lookups per PICO aspect)

### Phase 2: Retrieval

No LLM calls. All API.

| Source | API calls | Typical records |
|---|---|---|
| PubMed (esearch + efetch) | 3–6 | 100–500 |
| OpenAlex | 1–2 | 100–300 |
| Semantic Scholar | 1–2 | 100–300 |
| CrossRef (DOI resolution) | 0–50 | — |
| **Total pre-dedup** | | 300–1,100 |
| **After dedup** | | 200–700 |

**Wall time:** 1–3 min (parallel across sources; PubMed batch efetch is the bottleneck)

### Phase 3: Screening

Assumes 500 articles, thorough mode (dual-pass Sonnet 4.6, batches of 20 in the historical estimate; the pipeline now uses batches of 50 in parallel slices, so batch counts below would be roughly 10 per pass).

| Pass | Model | Batches | Input tokens/batch | Total input | Total output |
|---|---|---|---|---|---|
| Pass 1 | Sonnet 4.6 | 25 | ~7,000 | ~175,000 | ~8,000 |
| Pass 2 | Sonnet 4.6 | 25 | ~7,000 | ~175,000 | ~8,000 |
| Borderline re-screen (est. 10%) | Sonnet 4.6 | 3 | ~7,000 | ~21,000 | ~2,400 |

**Total Phase 3 (thorough):** ~371,000 input tokens, ~18,400 output tokens
**Prompt caching benefit:** System prompt (~1,500 tokens) cached per batch. At 25 batches × 2 passes, saves ~75,000 input tokens (≈20% cost reduction).

**Fast mode** (Haiku pass 1 + Sonnet borderlines only):
- Haiku pass 1: ~175,000 input tokens (much cheaper per token)
- Sonnet borderlines only: ~21,000 input tokens
- Recommended for scoping reviews or when speed matters more than dual-pass rigor.

**Wall time (thorough):** 8–18 min (parallel batch gather within each pass)

### Phase 4: Enrichment + Quality

Assumes 20 included articles.

| Stage | Model | Calls | Input tokens | Output tokens |
|---|---|---|---|---|
| Data extraction | Sonnet 4.6 | 1 | ~15,000 | ~8,000 |
| Quality appraisal | Sonnet 4.6 | 1–3 | ~20,000 | ~6,000 |
| Evidence classification | — (rule-based) | 0 | — | — |
| Citation chasing (Haiku screening) | Haiku 4.5 | 2–5 | ~15,000 | ~3,000 |

**API calls (no LLM):**
- Semantic Scholar batch_lookup: 1 call per 100 articles (client batch size; the API allows 500)
- OpenAlex get_work: 1 call per article (~20 calls)
- CrossRef get_references: 1 call per included article (~20 calls)
- Semantic Scholar get_citations: 1 call per included article (~20 calls)

**Wall time:** 5–15 min (enrichment is async/parallel; citation chasing has the most API calls)

### Phase 5: Synthesis + Output

Assumes 20 included articles.

| Stage | Model | Calls | Input tokens | Output tokens |
|---|---|---|---|---|
| Gap analysis | Sonnet 4.6 | 1 | ~6,000 | ~1,500 |
| Synthesis | Opus 4.7 | 1 | ~18,000 | ~5,000 |
| Executive summary (output_formatter) | Opus 4.7 | 1 | ~20,000 | ~3,000 |

**Wall time:** 3–8 min (Opus is slower but used only for final synthesis)

### Phase 6: Iteration (per loop)

Assumes 2–3 suggested queries, returning ~100 new candidate articles, with ~5 new includes.

| Stage | Model | Calls | Input tokens | Output tokens |
|---|---|---|---|---|
| PubMed retrieval (per query) | — | 2–4 | — | — |
| Screening new articles (fast mode) | Haiku 4.5 | 2–3 | ~14,000 | ~2,800 |
| Borderline re-screen | Sonnet 4.6 | 1 | ~4,000 | ~800 |
| Data extraction (new includes only) | Sonnet 4.6 | 1 | ~4,000 | ~2,000 |
| Quality appraisal (new includes only) | Sonnet 4.6 | 1 | ~5,000 | ~1,500 |
| Gap analysis (updated set) | Sonnet 4.6 | 1 | ~7,000 | ~1,500 |
| Re-synthesis | Opus 4.7 | 1 | ~20,000 | ~5,000 |

**Wall time per iteration:** 5–10 min

---

## Cost Estimates (April 2026 pricing)

| Model | Input $/MTok | Output $/MTok | Cached input $/MTok |
|---|---|---|---|
| Haiku 4.5 | $0.80 | $4.00 | $0.08 |
| Sonnet 4.6 | $3.00 | $15.00 | $0.30 |
| Opus 4.7 | $15.00 | $75.00 | $1.50 |

**Full review cost estimate (500 articles → 20 included, thorough screening, no iteration):**
- Phase 1 (Haiku + Sonnet): ~$0.15
- Phase 3 screening (Sonnet thorough): ~$1.40 (cache reduces by ~20% → ~$1.10)
- Phase 4 (Sonnet + Haiku): ~$0.25
- Phase 5 synthesis (Opus): ~$0.75
- **Total estimate: ~$2.25–$2.75 per full systematic review**

Scoping review with fast screening: ~$1.00–$1.50.
Each additional iteration: ~$0.50–$1.00.

---

## API Rate Limits

| API | Limit | Strategy |
|---|---|---|
| PubMed (NCBI) | 10 req/s with key | Token bucket; 3 req/s without key |
| OpenAlex | ~10 req/s (polite pool) | Set OPENALEX_EMAIL env var |
| CrossRef | 10 req/s (polite pool) | Set CROSSREF_MAILTO env var |
| Semantic Scholar | shared pool without key (frequently 429); 1 req/s with key | Set SEMANTIC_SCHOLAR_API_KEY; without it the source may be reported as degraded |
| Europe PMC | ~5 req/s | No auth required |

Rate limits enforced by `src/utils/rate_limiter.py` (token bucket, shared across all clients).

---

## Session Architecture

The pipeline's 6 human checkpoints correspond to up to 6 natural session break points; most
reviews take about 4–6 sessions for a full systematic review:

| Session | Stages | Natural break |
|---|---|---|
| 1 | Stages 1–3 (review type, PICO, protocol) | After protocol approval |
| 2 | Stages 4–7 (queries, retrieval, aggregation, dedup) | After dedup + PRISMA count |
| 3 | Stages 8–9.6 (screening, citation chasing, retrieval list, PDF download) | Library retrieval break |
| 4 | Stages 10–12 (full-text eligibility, extraction, enrichment) | After Checkpoint 5 |
| 5 | Stages 13–15 (quality appraisal, evidence classification, gap analysis) | After Checkpoint 6 |
| 6 | Stage 16 (synthesis + output) | Final artifacts |
| 6+ | Iteration loop (optional) | Per iteration |

Every session break is state-safe: `state.json` is atomically written after each stage.
