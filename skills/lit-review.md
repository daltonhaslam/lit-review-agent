---
name: lit-review
description: Use when the user invokes /lit-review to run an academic-physician-grade systematic or scoping literature review. Covers all pipeline stages from PICO formulation through output package. Session-safe with state persistence.
---

# Lit Review Agent — Skill v1

Academic-physician-grade systematic literature review assistant. Covers all 16 stages of the pipeline (question formulation through final output package). Session-safe: state is persisted after every stage; you can safely end and resume at any human checkpoint.

---

## On Activation

**Always start here — do not skip this step:**

```bash
python3 src/agent/state.py --scan reviews/
```

**If in-progress reviews are found:**
Present them clearly (review ID, status, question preview, last updated). Ask:
> "I found [N] in-progress review(s). Would you like to resume one, or start a new review?"

If resuming: note the review ID, load state, and route to the correct stage based on `status` (see Stage Routing below).

**If no in-progress reviews:**
Ask: "What is your research question?"
Then proceed to New Review Initialization.

---

## New Review Initialization

When starting a new review:
1. Ask for the research question (if not already provided).
2. Generate a slug: `{2-3 topic keywords}-{YYYY-MM}` (e.g., `icu-mobility-los-2026-04`). Use today's date.
3. Create the review directory and initialize state:

```bash
python3 - <<'EOF'
import sys, json
from pathlib import Path
sys.path.insert(0, ".")
from src.agent import state as sm
from src.models.review import Review, ScreeningMode

review_dir = Path("reviews/{SLUG}")
review = Review(
    review_id="{SLUG}",
    raw_question="{QUESTION}",
    screening_mode=ScreeningMode.thorough,
)
sm.save(review, review_dir)
print(json.dumps({"review_id": review.review_id, "review_dir": str(review_dir)}))
EOF
```

4. Confirm: "Review initialized. Starting Stage 1: Review Type Classification."
5. Proceed to Stage 1.

---

## Stage Routing

Route based on `review.status` from `state.json`:

| Status | Route to |
|---|---|
| `new` | Stage 1: Review Type Classification |
| `review_type_classified` | Stage 2: Fundamental Four + PICO Formulation |
| `pico_formulated` | Checkpoint 1: PICO Approval |
| `protocol_generated` | Checkpoint 2: Protocol Approval (or Stage 4 if protocol already approved by user) |
| `queries_built` | Tell user Phase 1 complete; queries ready for Session 2 retrieval |
| `retrieved` | Stage 6: Aggregation |
| `aggregated` | Stage 7: Deduplication |
| `deduplicated` | Checkpoint 3 — show PRISMA identified count; offer to proceed to Stage 8 |
| `abstract_screening` | Stage 8.5: Citation Chasing |
| `citation_chased` | Stage 9: Retrieval List Generation |
| `retrieval_list_generated` | Stage 9.5: Automated PDF Download |
| `pdfs_downloaded` | Stage 9.6 (optional): Manual Browser Retrieval → Checkpoint 4 |
| `fulltext_assessed` | Checkpoint 5: Included Set Approval |
| `data_extracted` | Stage 12: Enrichment |
| `enriched` | Stage 13: Quality Appraisal |
| `quality_appraised` | Stage 14: Evidence Classification |
| `evidence_classified` | Stage 15: Gap Analysis |
| `abstract_screened_iteration` | Legacy status (older runs only); iteration_manager.merge now sets `fulltext_assessed` → resume at Stage 11 |
| `gap_analyzed` | Checkpoint 6: Iteration Decision |
| `synthesized` | Stage 16 Step 2: Output Formatting |
| `complete` | Review complete — present final summary |

---

## Stage 1: Review Type Classification

Format context for Claude:
```bash
python3 -m src.skills.review_type_classifier --review-dir reviews/{SLUG} --prepare
```

Read the JSON output. Based on the `question` and `review_type_options` provided, classify the review type and formulate a rationale. Then save your classification:
```bash
python3 -m src.skills.review_type_classifier --review-dir reviews/{SLUG} --save --input-json '{"review_type": "{TYPE}", "rationale": "{RATIONALE}"}'
```

Present the result to the user:
> "Based on your question, I recommend a **{review_type}** review ({depth} coverage).
> Rationale: {rationale}
>
> Does this review type fit your intent, or would you prefer a different approach?"

If the user wants a different type, re-run `--save` with the corrected type:
```bash
python3 -m src.skills.review_type_classifier --review-dir reviews/{SLUG} --save --input-json '{"review_type": "{CORRECTED_TYPE}", "rationale": "{RATIONALE}"}'
```

Once confirmed, proceed to Stage 2.

---

## Stage 2: Fundamental Four + PICO Formulation

Ask the four Fundamental Four questions **one at a time** and collect all four answers before calling the script. Present them conversationally — do not batch them into a form.

**Question 1:** "What do we already know about this topic?"
*(Wait for user response.)*

**Question 2:** "What do we NOT know — what is the gap this review is trying to fill?"
*(Wait for user response.)*

**Question 3:** "What specifically should this review establish or clarify?"
*(Wait for user response.)*

**Question 4:** "Why does it matter clinically or operationally — what decision does this evidence support?"
*(Wait for user response.)*

After all four answers are collected, format context for Claude:
```bash
python3 -m src.skills.pico_refiner \
  --review-dir reviews/{SLUG} \
  --answers-json '{JSON_ESCAPED_ANSWERS}' \
  --prepare
```

The `--answers-json` value must be a valid JSON string with keys:
`what_we_know`, `what_we_dont_know`, `what_we_should_know`, `why_we_should_know`

Read the JSON output. Based on the question, review type, and Fundamental Four answers, build the full PICO(S) framework — be specific and PICO-derived. Then save:
```bash
python3 -m src.skills.pico_refiner --review-dir reviews/{SLUG} --save --input-json '{FULL_PICO_JSON}'
```

The `--input-json` value must be JSON with keys: `pico` (object with P, I, C, O list, S), `inclusion_criteria` (list), `exclusion_criteria` (list), `fundamental_four` (object with four keys).

Proceed to Checkpoint 1.

---

## Checkpoint 1: PICO Approval

Parse the pico_refiner output and present the PICO clearly:

> **Population (P):** {P}
> **Intervention (I):** {I}
> **Comparator (C):** {C}
> **Outcomes (O):**
> - {O[0]} *(primary)*
> - {O[1]}
> - ...
> **Study design (S):** {S or "No restriction"}
>
> **Inclusion criteria:**
> - {criterion}
>
> **Exclusion criteria:**
> - {criterion}
>
> Does this PICO accurately capture your research question? If anything needs adjustment, tell me what to change.

If the user requests changes, re-run `pico_refiner` with updated answers or adjusted context. After approval, save a note and proceed.

**After PICO approval — save state and offer a break:**
> "PICO approved and saved. ✓ You may safely end this session now and resume with `/lit-review` in a new session, or continue to Stage 3 (Protocol Generation)."

If ending: **→ Run Session Note Procedure before closing.**

If the user continues, proceed to Stage 3.

---

## Stage 3: Protocol Generation

Warn the user before running:
> "I'll now generate a PROSPERO-style protocol based on your PICO. **This action locks the protocol** — it cannot be regenerated after approval, which preserves pre-registration integrity. Proceed?"

After confirmation, format context for Claude:
```bash
python3 -m src.skills.protocol_generator --review-dir reviews/{SLUG} --prepare
```

Read the JSON output. Using the PICO, review metadata, and `protocol_structure` provided, write the full PROSPERO-style protocol — every section concrete, no placeholder text. Then save:
```bash
python3 -m src.skills.protocol_generator --review-dir reviews/{SLUG} --save --input-json '{"protocol_text": "{FULL_MARKDOWN_PROTOCOL}"}'
```

Display the saved protocol to the user:
```bash
cat reviews/{SLUG}/protocol.md
```

Proceed to Checkpoint 2.

---

## Checkpoint 2: Protocol Approval

> "Here is your research protocol. Review it carefully — once you approve, the protocol is locked and cannot be modified.
>
> [Protocol content above]
>
> Do you approve this protocol?"

If the user requests changes: explain that the protocol cannot be regenerated once locked. If they haven't approved yet, re-run pico_refiner with updates, then re-run protocol_generator.

After approval:
> "Protocol locked at {protocol_locked_at}. ✓ Protocol file saved to `reviews/{SLUG}/protocol.md`.
>
> **Safe session break point.** You may end this session now. When you resume with `/lit-review`, I will continue with Stage 4 (Query Construction)."

If ending: **→ Run Session Note Procedure before closing.**

If the user wants to continue immediately, proceed to Stage 4.

---

## Stage 4: Query Construction

Run MeSH lookups and format context for Claude:
```bash
python3 -m src.skills.query_builder --review-dir reviews/{SLUG} --prepare
```

Read the JSON output. The `pubmed_steps_from_mesh` field contains the stepwise PubMed query history already built from MeSH lookups. Generate the three database queries using the format rules below, then save:

- **`openalex_query`**: plain Boolean keyword string (AND/OR/NOT allowed; no MeSH tags like `[Mesh]`, `[tiab]`, `[pt]`)
- **`europepmc_query`**: plain Boolean keyword string (AND/OR/NOT allowed; no MeSH tags)
- **`semantic_scholar_query`**: natural-language phrase ONLY — no AND/OR/NOT, no parentheses, no field tags; write it as a short phrase a human would type into a search bar (e.g., `"pediatric observation hospitalization children hospital trend"`)

Refine `pubmed_steps` only if needed. Then save:
```bash
python3 -m src.skills.query_builder --review-dir reviews/{SLUG} --save --input-json '{"pubmed_steps": [...], "openalex_query": "...", "semantic_scholar_query": "...", "europepmc_query": "..."}'
```

Present the PubMed stepwise query history to the user in a readable format:

> **PubMed Stepwise Query History**
>
> | Step | Query | Rationale |
> |---|---|---|
> | #1 | "Heart Failure"[Mesh] | Population (P) — MeSH controlled vocabulary |
> | #2 | heart failure*[tiab] OR cardiac failure*[tiab] | Population (P) — free-text synonyms |
> | #3 | #1 OR #2 | Population (P) — MeSH OR free-text |
> | ... | ... | ... |
>
> **OpenAlex:** {openalex_query}
> **Semantic Scholar:** {semantic_scholar_query}
> **EuropePMC:** {europepmc_query}

If there are MeSH warnings, present them:
> ⚠️ {warning}

Ask the user to review and confirm or request adjustments.

After confirmation:
> "Queries saved. ✓ **Phase 1 (Question Formulation) complete.**
>
> **Safe session break point.** In Session 2, retrieval will run across PubMed, OpenAlex, and Semantic Scholar using these queries. End this session when ready; resume with `/lit-review`."

If ending: **→ Run Session Note Procedure before closing.**

If the user wants to continue immediately, proceed to Stage 5.

---

## Stage 5: Multi-Source Retrieval

Ask the user which sources to search (default: PubMed + OpenAlex + Semantic Scholar) and whether to add Europe PMC. Also confirm the year range if relevant to the protocol.

Then run retrieval in parallel:
```bash
python3 -m src.skills.multi_source_retriever \
  --review-dir reviews/{SLUG} \
  --sources pubmed,openalex,semantic_scholar \
  --max-per-source 500
```

Add `--year-from YYYY --year-to YYYY` if a year range was specified in the protocol.

Display results:
> **Retrieval complete.**
>
> | Source | Records retrieved |
> |---|---|
> | PubMed | {by_source.pubmed} |
> | OpenAlex | {by_source.openalex} |
> | Semantic Scholar | {by_source.semantic_scholar} |
> | **Total (pre-dedup)** | {total} |

If `retrieval_errors` contains any entries, surface them to the user before proceeding:
> ⚠️ **{source}** returned 0 results with error: `{error}`
> Check `config/api_keys.yaml` or retry.

For Semantic Scholar specifically: the free tier rate-limits quickly on large queries. If you see a 429 error, instruct the user to obtain a free API key at semanticscholar.org/product/api and add it as `semantic_scholar_api_key` in `config/api_keys.yaml`.

Then automatically proceed to Stage 6.

---

## Stage 6: Aggregation (DOI Resolution)

Run the aggregator to resolve any missing DOIs via CrossRef:
```bash
python3 -m src.skills.aggregator --review-dir reviews/{SLUG}
```

This is a silent background step. Show a brief status line only if DOIs were resolved:
> "DOI resolution: {dois_resolved} missing DOIs resolved via CrossRef."

Then automatically proceed to Stage 7.

---

## Stage 7: Deduplication

```bash
python3 -m src.skills.deduplicator --review-dir reviews/{SLUG}
```

Display the deduplication report:
> **Deduplication complete.**
>
> - Records before deduplication: {before}
> - Duplicates removed: {removed} ({by_method.doi_match} DOI match, {by_method.title_similarity} title similarity, {by_method.author_year_fallback} author+year fallback)
> - **PRISMA `identified` count: {prisma_identified}** ← this is your PRISMA flow starting point

Then reach **Checkpoint 3**.

---

## Checkpoint 3: Post-Retrieval Review

> "**Phase 2 (Retrieval) complete.** Here is your PRISMA progress so far:
>
> - Identified (post-dedup): {prisma_identified} records
>
> **Safe session break point.** Phase 3 (Screening) is next — title/abstract screening using dual-pass LLM review. End this session when ready; resume with `/lit-review`."

If ending: **→ Run Session Note Procedure before closing.**

If the user wants to continue immediately, proceed to Stage 8.

---

## Stage 8: Abstract Screening

Inform the user:
> "I will screen all abstracts using parallel sub-agents — each sees the full PICO context cold, so screening calibration stays consistent across the whole set."

### Step 1 — Probe pending count (also handles auto-resume)

```bash
python3 -m src.skills.abstract_screener --review-dir reviews/{SLUG} --prepare \
  --batch-size 1 --batch-offset 0
```

Read the JSON output. Note `total_articles`, `pending_articles`, `auto_resume_offset`, and `effective_offset`.
- If `pre_filtered_total > 0`, inform the user: "Pre-filtered {pre_filtered_total} articles outside the protocol date range — skipping to in-range articles."
- If `auto_resume_offset > 0`, inform the user: "Resuming from article {auto_resume_offset} — {auto_resume_offset} decisions already saved."

### Step 2 — Choose mode

Compute `N = min(5, max(1, ceil(pending_articles / 200)))`.

- If `pending_articles < 50`, drop to **Serial mode** (below).
- Otherwise, run **Parallel mode**.

### Parallel mode (default)

**Step 3 — Prepare N slices in parallel.** In a single message, run all slice prepares simultaneously. Each call returns the same PICO/criteria/screening_task fields plus that slice's `articles` and `slice_indices`:

```bash
# Run these in parallel (one Bash call per slice in a single message):
python3 -m src.skills.abstract_screener --review-dir reviews/{SLUG} --prepare --slice-of N --slice-index 0
python3 -m src.skills.abstract_screener --review-dir reviews/{SLUG} --prepare --slice-of N --slice-index 1
# ... up through --slice-index (N-1)
```

**Step 4 — Spawn N cold-start sub-agents in parallel.** In a single message, make N `Agent` tool calls (use `subagent_type: "general-purpose"`). Each prompt must be self-contained — sub-agents inherit no parent context — and contain:
- The full PICO (P/I/C/O/S), inclusion criteria, exclusion criteria, and `screening_task` text from the slice prepare output (identical across all slices).
- That slice's `articles` array verbatim.
- An instruction to respond with **JSON only**, in the exact form:
  ```json
  {"decisions": [{"article_index": N, "decision": "include|exclude|borderline", "reason": "1-2 sentences", "reason_category": "..."|null}]}
  ```
  `reason_category` for `exclude` decisions must be one of: `wrong_population`, `wrong_intervention`, `wrong_comparator`, `wrong_outcome`, `wrong_study_design`, `not_original_research`, `other`. `null` for include/borderline.

**Step 5 — Merge and validate.** When all sub-agents return, parse each JSON payload and concatenate `decisions` arrays:
```python
merged = []
for slice_result in slice_results:
    merged.extend(slice_result["decisions"])
```
Validate: every value in the union of all `slice_indices` must appear in `merged` exactly once. If a sub-agent returned malformed JSON or missing indices, re-run that single slice (only that slice) before saving.

**Step 6 — Save once.**
```bash
python3 -m src.skills.abstract_screener --review-dir reviews/{SLUG} --save \
  --offset 0 --decisions-json '{MERGED_DECISIONS_JSON}'
```
Output should report `"complete": true` and `"pending_remaining": 0`. If not, run a final non-slice `--prepare --batch-size 1` to identify which articles are still pending and re-screen them.

### Serial mode (fallback for `pending_articles < 50` or debugging)

**REPEAT — use `CURRENT_OFFSET = effective_offset` on the first iteration, then advance by 50:**

**Step 1 — Get batch:**
```bash
python3 -m src.skills.abstract_screener --review-dir reviews/{SLUG} --prepare \
  --batch-size 50 --batch-offset {CURRENT_OFFSET}
```

**Step 2 — Screen the batch.** Read the `articles`, `pico`, `inclusion_criteria`, `exclusion_criteria`, and `screening_task` fields. For each article, apply the PICO criteria and produce a decision: `include`, `exclude`, or `borderline`. Construct:
```json
{"decisions": [{"article_index": N, "decision": "include|exclude|borderline", "reason": "1-2 sentences", "reason_category": "..."|null}]}
```

**Step 3 — Save decisions:**
```bash
python3 -m src.skills.abstract_screener --review-dir reviews/{SLUG} --save \
  --offset {CURRENT_OFFSET} --decisions-json '{DECISIONS_JSON}'
```

Check the output. If `"complete": true`, stop. Otherwise, advance `CURRENT_OFFSET` by 50 and repeat.

**END REPEAT**

When `complete: true`, display the final summary:

> **Abstract Screening complete.**
>
> | Decision | Count |
> |---|---|
> | Include | {include} |
> | Borderline | {borderline} |
> | Exclude | {exclude} |
> | **Total screened** | {total_articles} |
>
> **Exclusion reasons:** {by_reason as bullet list}

If borderlines > 0:
> "**{borderline} articles are borderline** and require full-text review before a final decision. These are included in the retrieval list. You will review them after obtaining PDFs."

**Stage 8 complete.** Before proceeding:
1. Review the screening summary above. Does it look correct?
2. **Context check:** If you have been screening a large article set (many batches), consider whether this session should end here. Starting Stage 8.5 is quick but a fresh session always helps.

> "Abstract screening complete. {include} includes, {borderline} borderline, {exclude} excluded. Would you like to continue to Stage 8.5 (Citation Chasing), or end this session and resume later?"

Pause here. Wait for user confirmation before proceeding to Stage 8.5.

---

## Stage 8.5: Citation Chasing

Inform the user:
> "I'll run CrossRef + Semantic Scholar citation chasing on the abstract-screening includes, then screen any new candidates with parallel sub-agents."

### Step 1 — Build the candidate set

Run the network pipeline once (CrossRef references + S2 citations + identifier enrichment + keyword pre-filter — may take 2–5 minutes). This call writes `_citation_candidates.json`; subsequent slice calls reuse the file.

```bash
python3 -m src.skills.citation_chaser --review-dir reviews/{SLUG} --prepare
```

Read the JSON output. Note `total_candidates`. If `total_candidates == 0`, skip screening and finalize:
```bash
python3 -m src.skills.citation_chaser --review-dir reviews/{SLUG} --save --input-json '{"decisions": []}'
```
Then jump to the **Display** block at the end of this stage.

### Step 2 — Choose mode

- If `total_candidates < 50`, drop to **Serial mode** (below).
- Otherwise, run **Parallel mode**.

### Parallel mode (default, `total_candidates ≥ 50`)

Compute `N = min(5, max(2, ceil(total_candidates / 200)))`.

**Step 3 — Prepare N slices in parallel.** In a single message, run all slice prepares simultaneously. Each call reads the persisted candidates file and returns just that slice plus the same `pico_context` and `screening_task` text:

```bash
# Run these in parallel (one Bash call per slice in a single message):
python3 -m src.skills.citation_chaser --review-dir reviews/{SLUG} --prepare --slice-of N --slice-index 0
python3 -m src.skills.citation_chaser --review-dir reviews/{SLUG} --prepare --slice-of N --slice-index 1
# ... up through --slice-index (N-1)
```

**Step 4 — Spawn N cold-start sub-agents in parallel.** In a single message, make N `Agent` tool calls (use `subagent_type: "general-purpose"`). Each prompt must be self-contained — sub-agents inherit no parent context — and contain:
- `pico_context` and `screening_task` from the slice prepare output (identical across all slices).
- That slice's `candidates` array verbatim.
- An instruction to respond with **JSON only**, in the exact form:
  ```json
  {"decisions": [{"index": N, "decision": "include|exclude|borderline", "rationale": "1 sentence"}]}
  ```

**Step 5 — Merge and validate.** When all sub-agents return, parse each JSON payload and concatenate `decisions` arrays:
```python
merged = []
for slice_result in slice_results:
    merged.extend(slice_result["decisions"])
```
Validate: every value in the union of all `slice_indices` must appear in `merged` exactly once. If a sub-agent returned malformed JSON or missing indices, re-run that single slice (only that slice) before saving.

**Step 6 — Save once.**
```bash
python3 -m src.skills.citation_chaser --review-dir reviews/{SLUG} --save --input-json '{MERGED_DECISIONS_JSON}'
```

### Serial mode (fallback for `total_candidates < 50`)

Screen the candidates in `candidates` against `pico_context` using the `screening_task` instructions. Only articles with `screening_decision == "include"` were used as seeds — borderlines do not generate citation-chased candidates. Construct:
```json
{"decisions": [{"index": N, "decision": "include|exclude|borderline", "rationale": "1 sentence"}]}
```

Then save:
```bash
python3 -m src.skills.citation_chaser --review-dir reviews/{SLUG} --save --input-json '{"decisions": [...]}'
```

Display:
> **Citation chasing complete.**
>
> | | Count |
> |---|---|
> | Backward candidates (references) | {backward_candidates} |
> | Forward candidates (citing papers) | {forward_candidates} |
> | After deduplication | {after_dedup} |
> | **New includes added** | {new_includes} |
>
> PRISMA citation_chasing_added: {new_includes}
>
> Search log updated: `reviews/{SLUG}/search_log.md`

If new_includes > 0:
> "✓ {new_includes} new articles added via citation chasing and appended to the screened set. Stage 9 will generate a single combined retrieval list covering both database-searched and citation-chased articles."

If new_includes == 0:
> "No new articles added via citation chasing."

Then automatically proceed to Stage 9.

---

## Stage 9: Retrieval List Generation

Generates a single combined retrieval list covering both database-searched articles and any citation-chased articles added at Stage 8.5.

```bash
python3 -m src.skills.retrieval_list_generator --review-dir reviews/{SLUG}
```

Display:
> **Retrieval list generated.**
>
> - Articles to retrieve: {total} ({include} include + {borderline} borderline)
> - Open access (PDF available now): {open_access}
> - Requires library retrieval: {total - open_access}
>
> File saved to: `reviews/{SLUG}/retrieval_list.csv`

Then proceed to Stage 9.5.

---

## Stage 9.5: Automated PDF Download

Run immediately after Stage 9 (no user input required — pure API calls, no LLM tokens used):

```bash
python3 -m src.skills.pdf_downloader --review-dir reviews/{SLUG}
```

Display the download report:
> **Automated PDF download complete.**
>
> | | Count |
> |---|---|
> | PDFs downloaded | {downloaded} |
> | Not found (library needed) | {not_found} |
> | Download errors | {failed} |
>
> **Sources used:** existing OA URL: {sources.existing_url} | Unpaywall: {sources.unpaywall} | OpenAlex: {sources.openalex} | PMC: {sources.pmc} | Europe PMC: {sources.europe_pmc} | Crossref: {sources.crossref}
>
> `retrieval_list.csv` updated with `pdf_status` column. `library_request.csv` written with {not_found} articles for library submission.
>
> **PDF naming convention for library-obtained files:** Name by PMID (`{pmid}.pdf`, preferred) or by DOI with `/` and `:` replaced by `_` (e.g., `10.1001_jama.2022.001.pdf`). Place in `reviews/{SLUG}/pdfs/`.

If `library_request.csv` is empty (everything was OA), skip Stage 9.6 and go directly to **Checkpoint 4**. Otherwise, offer Stage 9.6.

---

## Stage 9.6: Manual Browser Retrieval (Optional)

When `library_request.csv` is non-empty after Stage 9.5, this optional stage opens all remaining articles as Chrome tabs so the user can download PDFs natively from publisher pages using their own legitimate access (institutional subscription, personal subscription, or open-access mirror). This is plain manual retrieval, not automated bypass — the user only downloads what their own access already entitles them to. Files arriving in `~/Downloads` are then matched, renamed, and moved into `pdfs/` automatically.

If `library_request.csv` is empty, skip directly to **PDF Readiness Check** below.

### Step 1 — Check remaining count and offer the stage

Count remaining rows:
```bash
wc -l < reviews/{SLUG}/library_request.csv
```

If the count (minus 1 for the header) is 0, skip. Otherwise, ask:
> "**Stage 9.6 (optional):** {N} articles still need manual fetch. Open them all in Chrome now? Each article opens as one tab at `https://doi.org/{DOI}` (or PubMed URL when no DOI). Download PDFs natively wherever your own institutional or personal access allows, then I'll match them by filename and move them to `pdfs/`. Articles you can't access stay in `library_request.csv` for your hospital library to fetch. (yes / no / skip-to-4)"

If user says `no` or `skip-to-4`: go directly to **Checkpoint 4**. If `yes`: continue.

### Step 2 — Filter against current pdfs/ (idempotency)

Run reconcile first so any files dropped in `pdfs/` since Stage 9.5 are visible:
```bash
python3 -m src.skills.pdf_downloader --review-dir reviews/{SLUG} --reconcile-disk
```

Then build the URL list. For each row in `library_request.csv` whose `pdf_status` (in `retrieval_list.csv`) is still not `downloaded`:
- Prefer `https://doi.org/{doi}` when `doi` is non-empty.
- Fall back to `https://pubmed.ncbi.nlm.nih.gov/{pmid}` when only `pmid` is present.
- Skip and list separately as "no URL available" when both are blank.

### Step 3 — Open article pages in Chrome

These URLs are the articles' canonical DOI/PubMed pages — opening them does not bypass any paywall or access control. Each tab lets the user download the PDF only if their own login/subscription/library access already grants it.

```bash
open -a "Google Chrome" "URL1" "URL2" "URL3" ... "URLN"
```

If `N > 100`, warn the user first:
> "⚠️ Opening {N} tabs in Chrome. Recommend closing other Chrome windows first to free RAM. Continue? (yes / no)"

### Step 4 — Wait for user to finish downloading

Prompt:
> "Tabs opened. Download PDFs natively from each tab into `~/Downloads` — only where your own access allows. Reply 'done' when ready, or 'cancel' to abort and proceed to Checkpoint 4."

### Step 5 — Import + match + move + reconcile

```bash
python3 -m src.skills.pdf_downloader --review-dir reviews/{SLUG} --import-from-downloads
```

Read the JSON output. Display:
> **Stage 9.6 import complete:**
>
> - Imported and renamed: `{imported_count}`
> - Unmatched (left in `~/Downloads`): `{len(unmatched)}`
> - Still missing in retrieval_list.csv: `{still_missing}`

If any unmatched entries appear in the output:
> "These files couldn't be matched to a retrieval row:
> {for each u in unmatched: `  - {u['filename']} ({u['reason']})`}
>
> Manual fix: rename to `{PMID}.pdf` or `{DOI_safe}.pdf` and re-run `--import-from-downloads`."

### Step 6 — Loop or proceed

Ask:
> "Run another import pass? (yes / done)"

- `yes` → return to Step 5 (user added more downloads).
- `done` → proceed to **Checkpoint 4**.

Then reach **Checkpoint 4**.

---

## PDF Readiness Check (before Stage 10)

After PDFs have been obtained (or any time before running Stage 10), check how many are available:

```bash
python3 -m src.skills.pdf_downloader --review-dir reviews/{SLUG} --count-ready
```

Output: `{"total": N, "pdfs_ready": M, "not_found": K}`

Recommendation logic:
- **≥ 80% available** → Recommend proceeding; articles without PDFs will be assessed on abstract only
- **50–80%** → Recommend waiting for more PDFs, but offer to proceed; note which articles are missing
- **< 50%** → Warn that full-text assessment will be severely limited; ask user to confirm before continuing

> "PDFs ready for full-text assessment: {pdfs_ready} / {total} articles. Would you like to proceed to Stage 10 (Full-Text Assessment), wait for more PDFs, or take a break?"

If taking a break: state is saved at `pdfs_downloaded`. Resume by starting a new session — the agent will pick up at Stage 10.

---

## Checkpoint 4: Library Retrieval Break

> "**Phase 3 screening complete (database search + citation chasing).** Here is your PRISMA progress:
>
> - Identified (database): {prisma_counts.identified}
> - Citation chasing identified: {prisma_counts.citation_chasing_identified}
> - Citation chasing added to screened set: {prisma_counts.citation_chasing_added}
> - Screened (total): {prisma_counts.screened}
> - Excluded at screening: {sum(prisma_counts.excluded_screening.values())}
> - For retrieval / full-text review: {total eligible}
>
> **Action required before continuing:**
> 1. Open `reviews/{SLUG}/library_request.csv` — this is the minimal list of articles not found online; submit to your hospital library
> 2. Place library-obtained PDFs in `reviews/{SLUG}/pdfs/`
>    - Name each file by PMID: `{pmid}.pdf` (preferred)
>    - Or by DOI with `/` replaced by `_`: e.g., `10.1001_cardio.2022.001.pdf`
> 3. To see the full included set with PDF resolution status, open `reviews/{SLUG}/retrieval_list.csv` — the `pdf_status` column shows `downloaded`, `not_found`, or `skipped` for each article
> 4. Resume this review with `/lit-review` when PDFs are ready
>
> **Safe session break point.** End this session when ready."

**→ Run Session Note Procedure before closing.**

If the user has PDFs ready now, proceed to Stage 10. Otherwise, end session here.

---

## Stage 10: Full-Text Eligibility Assessment

Before running, confirm:
> "I'll now load the eligible articles and any PDFs in `reviews/{SLUG}/pdfs/`. I'll extract text from matched PDFs and assess each article against your PICO criteria. Articles without a PDF will be marked as pending. Proceed?"

### Step 1 — Probe pending count

```bash
python3 -m src.skills.fulltext_assessor --review-dir reviews/{SLUG} --prepare
```

Read `total_eligible`. Choose mode:
- **`total_eligible >= 50`** → Parallel mode (default).
- **`total_eligible < 50`** → Serial mode (single prepare/save).

Add `--pdf-dir /path/to/pdfs` to any invocation if PDFs are stored outside the default location.

### Step 2 — (Parallel mode) Compute slice cardinality

`N = min(8, max(1, ceil(total_eligible / 50)))` — full-text payloads are token-heavier than abstracts, so cap at 8 slices.

### Step 3 — (Parallel mode) Prepare each slice in parallel

```bash
# Run these in parallel (one Bash call per slice in a single message):
python3 -m src.skills.fulltext_assessor --review-dir reviews/{SLUG} --prepare --slice-of N --slice-index 0
python3 -m src.skills.fulltext_assessor --review-dir reviews/{SLUG} --prepare --slice-of N --slice-index 1
# ... up through --slice-index (N-1)
```

### Step 4 — (Parallel mode) Spawn N cold-start sub-agents

In a single message, make N `Agent` tool calls (use `subagent_type: "general-purpose"`). Each prompt must be self-contained and contain:
- The `assessment_task` text from the slice prepare output.
- The `pico_context`, `inclusion_criteria`, `exclusion_criteria` shared fields.
- That slice's `articles` array verbatim (each entry has `orig_idx`, `pdf_found`, `fulltext`, and possibly `pdf_low_confidence` + `pdf_match_warnings`).
- Instruction: respond with **JSON only** in this exact form:
  ```json
  {"decisions": [{"orig_idx": N, "decision": "include|exclude", "rationale": "specific criterion cited", "study_design": "e.g. RCT"}]}
  ```
- For any article where `pdf_found` is false: do NOT emit a decision (auto-marked pending — no PDF to assess).
- For any article where `pdf_low_confidence` is true but `pdf_found` is true: STILL assess it. The flag is advisory, not a skip — treat it as a prompt to sanity-check that the extracted `fulltext` actually matches the article's title/topic. Only if the content genuinely belongs to a different paper, emit `{"decision": "exclude", "rationale": "Wrong PDF content — extracted text does not match article title"}`. Otherwise assess normally against the criteria. (Do NOT silently drop it — an unassessed low-confidence article becomes a false `pending`.)

### Step 5 — Merge and save

Concatenate all sub-agent `decisions` arrays into a single merged list. Then save once:

```bash
python3 -m src.skills.fulltext_assessor --review-dir reviews/{SLUG} --save --input-json '{"decisions": [MERGED_DECISIONS]}'
```

### Serial mode (fallback for `total_eligible < 50` or debugging)

Read the prepare output's `articles` array and `assessment_task`. Assess every article where `fulltext` is not null against `pico_context`, `inclusion_criteria`, and `exclusion_criteria`. When `pdf_low_confidence` is true, still assess it but verify the extracted text matches the article's title/topic first — exclude with rationale "Wrong PDF content — extracted text does not match article title" only if the content genuinely belongs to a different paper (the flag is advisory, not a skip):

```json
{"decisions": [{"orig_idx": N, "decision": "include|exclude", "rationale": "specific criterion cited", "study_design": "e.g. RCT"}]}
```

```bash
python3 -m src.skills.fulltext_assessor --review-dir reviews/{SLUG} --save --input-json '{"decisions": [...]}'
```

### Display

> **Full-text assessment complete.**
>
> | | Count |
> |---|---|
> | Eligible for full-text | {eligible_for_fulltext} |
> | Assessed | {assessed} |
> | Included | {included} |
> | Excluded at full-text | {excluded_fulltext} |
> | Pending (no PDF) | {pending_retrieval} |
> | Low-confidence PDF matches | {low_confidence_count} |

If `pending_retrieval > 0`:
> ⚠️ {pending_retrieval} articles could not be matched to a PDF. Check that PDFs are named by PMID (`{pmid}.pdf`) or DOI (`{doi_safe}.pdf`) and placed in `reviews/{SLUG}/pdfs/`. Re-run Stage 10 after adding missing PDFs.

If `low_confidence_count > 0`:
> ⚠️ {low_confidence_count} PDFs had suspiciously short extracted text or low title-word coverage against the article. These were still assessed (the flag is advisory); the sub-agents were told to exclude only genuine wrong-PDF content. Inspect `pdf_match_warnings` in the prepare output and replace any truly mismatched PDFs in `reviews/{SLUG}/pdfs/`.

Then reach **Checkpoint 5**.

---

## Checkpoint 5: Included Set Approval

Parse `articles_included.json` and display the included set:

> "**Full-text assessment complete.** Here is your final included set:
>
> | # | Title | Authors | Year | Journal | Study design |
> |---|---|---|---|---|---|
> | 1 | {title} | {first author et al.} | {year} | {journal} | {study_design} |
> | ... |
>
> **PRISMA flow so far:**
> - Identified: {prisma_counts.identified}
> - Screened: {prisma_counts.screened}
> - Excluded at screening: {sum(excluded_screening.values())}
> - Reports sought for full-text: {prisma_counts.eligibility} (assessed: {prisma_counts.fulltext_assessed}, not retrieved: {prisma_counts.not_retrieved})
> - Included: {prisma_counts.included}
>
> Do you approve this included set? You may request any manual adjustments before proceeding to Phase 4 (Enrichment + Quality Appraisal)."

If the user requests changes (add/remove an article), update `articles_included.json` and `prisma_counts` manually, then re-confirm.

After approval:
> "Included set approved ({prisma_counts.included} articles). ✓
>
> **Safe session break point.** Phase 4 (Enrichment + Quality Appraisal) is next. End this session when ready; resume with `/lit-review`."

If ending: **→ Run Session Note Procedure before closing.**

Update stage routing table: when status is `fulltext_assessed`, route to Checkpoint 5.

---

## Stage 11: Data Extraction

After the user approves the included set (Checkpoint 5), run data extraction:

> "I will extract structured data from all included articles using parallel sub-agents — each sees the full extraction task cold, so per-article extraction stays consistent across the whole set."

### Step 1 — Probe total

```bash
python3 -m src.skills.data_extractor --review-dir reviews/{SLUG} --prepare \
  --batch-size 1 --batch-offset 0
```

Read the JSON output. Note `total_articles`.

### Step 2 — Choose mode

Compute `N = min(5, max(1, ceil(total_articles / 100)))`.

- If `total_articles < 50`, drop to **Serial mode** (below).
- Otherwise, run **Parallel mode**.

### Parallel mode (default, `total_articles ≥ 50`)

**Step 3 — Prepare N slices in parallel.** In a single message, run all slice prepares simultaneously. Each call returns the same `extraction_task` text plus that slice's `articles` and `slice_indices`:

```bash
# Run these in parallel (one Bash call per slice in a single message):
python3 -m src.skills.data_extractor --review-dir reviews/{SLUG} --prepare --slice-of N --slice-index 0
python3 -m src.skills.data_extractor --review-dir reviews/{SLUG} --prepare --slice-of N --slice-index 1
# ... up through --slice-index (N-1)
```

**Step 4 — Spawn N cold-start sub-agents in parallel.** In a single message, make N `Agent` tool calls (use `subagent_type: "general-purpose"`). Each prompt must be self-contained — sub-agents inherit no parent context — and contain:
- The `extraction_task` text from the slice prepare output (identical across all slices).
- That slice's `articles` array verbatim (each entry has `orig_idx` and `article_text`).
- An instruction to respond with **JSON only**, in the exact form:
  ```json
  {"extractions": [{"orig_idx": N, "study_design": "...", "sample_size": N|null, "follow_up_duration": "...|null", "intervention_details": "...|null", "comparison_details": "...|null", "primary_outcomes": [...], "effect_sizes": "...|null", "secondary_outcomes": [...], "author_conclusions": "...|null", "funding_source": "...|null", "coi_declared": true|false|null}]}
  ```

**Step 5 — Merge and validate.** When all sub-agents return, parse each JSON payload and concatenate `extractions` arrays:
```python
merged = []
for slice_result in slice_results:
    merged.extend(slice_result["extractions"])
```
Validate: every value in the union of all `slice_indices` must appear in `merged` exactly once. If a sub-agent returned malformed JSON or missing indices, re-run that single slice (only that slice) before saving.

**Step 6 — Save once.**
```bash
python3 -m src.skills.data_extractor --review-dir reviews/{SLUG} --save \
  --offset 0 --input-json '{MERGED_EXTRACTIONS_JSON}'
```
Output should report `"complete": true`. Evidence table is written when complete.

### Serial mode (fallback for `total_articles < 50` or debugging)

**REPEAT — advance `CURRENT_OFFSET` by 50 each iteration, starting at 0:**

**Step 1 — Get batch:**
```bash
python3 -m src.skills.data_extractor --review-dir reviews/{SLUG} --prepare \
  --batch-size 50 --batch-offset {CURRENT_OFFSET}
```

**Step 2 — Extract data.** Read the `articles` array (each has `orig_idx` and `article_text`). Apply the `extraction_task` instructions to extract structured data. Construct:
```json
{"extractions": [{"orig_idx": N, "study_design": "...", "sample_size": N|null, "follow_up_duration": "...|null", "intervention_details": "...|null", "comparison_details": "...|null", "primary_outcomes": [...], "effect_sizes": "...|null", "secondary_outcomes": [...], "author_conclusions": "...|null", "funding_source": "...|null", "coi_declared": true|false|null}]}
```

**Step 3 — Save extractions:**
```bash
python3 -m src.skills.data_extractor --review-dir reviews/{SLUG} --save \
  --batch-size 50 --offset {CURRENT_OFFSET} --input-json '{EXTRACTIONS_JSON}'
```

Check the output. If `"complete": true`, stop. Otherwise, advance `CURRENT_OFFSET` by 50 and repeat.

**END REPEAT**

When `complete: true`, display:
> **Data extraction complete.**
>
> | Field | Coverage |
> |---|---|
> | Articles processed | {extraction_coverage.total} |
> | Study design | {extraction_coverage.with_study_design} ({extraction_coverage.with_study_design / extraction_coverage.total * 100:.0f}%) |
> | Primary outcomes | {extraction_coverage.with_primary_outcomes} ({extraction_coverage.with_primary_outcomes / extraction_coverage.total * 100:.0f}%) |
> | Effect sizes | {extraction_coverage.with_effect_sizes} ({extraction_coverage.effect_size_pct * 100:.0f}%) |
> | Author conclusions | {extraction_coverage.with_conclusions} ({extraction_coverage.with_conclusions / extraction_coverage.total * 100:.0f}%) |
> | Funding source | {extraction_coverage.with_funding} ({extraction_coverage.with_funding / extraction_coverage.total * 100:.0f}%) |
> | COI declared | {extraction_coverage.with_coi} |
> | Intervention details | {extraction_coverage.with_intervention} |
> | Comparison details | {extraction_coverage.with_comparison} |
> | Empty input (no PDF, no abstract) | {extraction_coverage.articles_with_empty_input} |
>
> **Data source:** {extraction_coverage.data_source_distribution}
>
> Evidence table written to: `reviews/{SLUG}/evidence_table.csv`

If `effect_size_pct < 0.10`, also display:
> ⚠️ Effect sizes extracted for only {effect_size_pct * 100:.0f}% of articles — synthesis will be narrative-only for most outcomes.

If the `warnings` array is non-empty, surface each warning verbatim with a ⚠️ prefix. Do not skip warnings — they reflect real data-quality gaps that will degrade synthesis if ignored. Typical warnings:
> ⚠️ author_conclusions populated for {n}/{total} ({pct}%) — extraction quality is degraded.
> ⚠️ funding_source extracted for only {n}/{total} ({pct}%) — expected ≥30% when full text is available. Check that PDFs include acknowledgments/funding sections.
> ⚠️ {n} articles had empty input text (no PDF and no abstract) — orig_idx {list}. Resolve by obtaining the PDFs and running `python -m src.skills.fulltext_assessor --review-dir reviews/{SLUG} --persist-pdf-text` then re-running Stage 11.

**Stage 11 complete.** Before proceeding:
1. Review the extraction output. Does it look correct?
2. **Context check:** Parallel mode is one orchestration turn — no mid-stage context risk. The serial fallback runs only when `total_articles < 50`, so a single batch covers it.

> "Data extraction complete ({extraction_coverage.total} articles). Would you like to continue to Stage 12 (Enrichment), or end this session and resume later?"

Pause here. Wait for user confirmation before proceeding to Stage 12.

---

## Stage 12: Enrichment (Citation Metadata)

```bash
python3 -m src.skills.enricher --review-dir reviews/{SLUG}
```

Fetches citation counts, citation velocity (citations/year), TLDRs, and concept tags via Semantic Scholar and OpenAlex. No LLM calls — pure API enrichment.

Display:
> **Enrichment complete.**
>
> | | Count |
> |---|---|
> | Articles enriched | {enriched} |
> | Semantic Scholar matched | {s2_matched} |
> | OpenAlex concept tags added | {openalex_matched} |

Then automatically proceed to Stage 13.

---

## Stage 13: Quality Appraisal

Run quality appraisal:

> "I will appraise the quality of all included articles using parallel sub-agents — each sees the instrument prompts cold so per-article scoring stays consistent across the whole set."

### Step 1 — Probe total

```bash
python3 -m src.skills.quality_appraiser --review-dir reviews/{SLUG} --prepare \
  --batch-size 1 --batch-offset 0
```

Read the JSON output. Note `total_articles`.

### Step 2 — Choose mode

Compute `N = min(5, max(1, ceil(total_articles / 100)))`.

- If `total_articles < 50`, drop to **Serial mode** (below).
- Otherwise, run **Parallel mode**.

### Parallel mode (default, `total_articles ≥ 50`)

**Step 3 — Prepare N slices in parallel.** In a single message, run all slice prepares simultaneously. Each slice's articles already include their auto-detected `instrument` and `appraisal_prompt`:

```bash
# Run these in parallel (one Bash call per slice in a single message):
python3 -m src.skills.quality_appraiser --review-dir reviews/{SLUG} --prepare --slice-of N --slice-index 0
python3 -m src.skills.quality_appraiser --review-dir reviews/{SLUG} --prepare --slice-of N --slice-index 1
# ... up through --slice-index (N-1)
```

**Step 4 — Spawn N cold-start sub-agents in parallel.** In a single message, make N `Agent` tool calls (use `subagent_type: "general-purpose"`). Each prompt must be self-contained — sub-agents inherit no parent context — and contain:
- The `appraisal_task` text from the slice prepare output (identical across all slices).
- That slice's `articles` array verbatim. Each article entry includes `orig_idx`, `instrument`, `article_text`, and `appraisal_prompt`. Instruct the sub-agent to apply each article's own `appraisal_prompt` to its `article_text`.
- An instruction to respond with **JSON only**, in the exact form:
  ```json
  {"appraisals": [{"orig_idx": N, "score": <0.0-1.0>, "notes": "<2-3 sentence quality summary>"}]}
  ```

**Step 5 — Merge and validate.** When all sub-agents return, parse each JSON payload and concatenate `appraisals` arrays:
```python
merged = []
for slice_result in slice_results:
    merged.extend(slice_result["appraisals"])
```
Validate: every value in the union of all `slice_indices` must appear in `merged` exactly once. If a sub-agent returned malformed JSON or missing indices, re-run that single slice (only that slice) before saving.

**Step 6 — Save once.**
```bash
python3 -m src.skills.quality_appraiser --review-dir reviews/{SLUG} --save \
  --offset 0 --input-json '{MERGED_APPRAISALS_JSON}'
```
Output should report `"complete": true`. Quality report is written when complete.

### Serial mode (fallback for `total_articles < 50` or debugging)

**REPEAT — advance `CURRENT_OFFSET` by 50 each iteration, starting at 0:**

**Step 1 — Get batch:**
```bash
python3 -m src.skills.quality_appraiser --review-dir reviews/{SLUG} --prepare \
  --batch-size 50 --batch-offset {CURRENT_OFFSET}
```

**Step 2 — Appraise quality.** Read the `articles` array. Each article has `orig_idx`, `instrument` (auto-detected), `article_text`, and `appraisal_prompt`. Apply the instrument-specific prompt to each article. Construct:
```json
{"appraisals": [{"orig_idx": N, "score": <0.0-1.0>, "notes": "<2-3 sentence quality summary>"}]}
```

**Step 3 — Save appraisals:**
```bash
python3 -m src.skills.quality_appraiser --review-dir reviews/{SLUG} --save \
  --batch-size 50 --offset {CURRENT_OFFSET} --input-json '{APPRAISALS_JSON}'
```

Check the output. If `"complete": true`, stop. Otherwise, advance `CURRENT_OFFSET` by 50 and repeat.

**END REPEAT**

When `complete: true`, display:
> **Quality appraisal complete.**
>
> | Instrument | Count | Median |
> |---|---|---|
> | {instrument} | {by_instrument_stats[instrument]["count"]} | {by_instrument_stats[instrument]["median"]} |
>
> Score distribution: mean={mean_quality}, median={median_quality}, min={min_quality}, max={max_quality}.
> Quality report written to: `reviews/{SLUG}/quality_report.md`

If `low_quality_count > 0`, also display:
> ⚠️ {low_quality_count} of {total_articles} articles scored below 0.5 — review `quality_report.md` and consider whether these low-quality studies should affect interpretation.

Then automatically proceed to Stage 14.

---

## Stage 14: Evidence Classification

```bash
python3 -m src.skills.evidence_classifier --review-dir reviews/{SLUG}
```

Assigns NHMRC evidence levels (I–IV) and flags surrogate outcomes. No LLM calls.

NHMRC hierarchy:
- Level I: Systematic reviews / meta-analyses
- Level II: RCTs
- Level III-1: Pseudo-randomised trials
- Level III-2: Prospective comparative studies
- Level III-3: Retrospective cohort, case-control
- Level IV: Case series, expert opinion, narrative reviews

Display:
> **Evidence classification complete.**
>
> | Level | Count |
> |---|---|
> | Level I | {count} |
> | Level II | {count} |
> | Level III-1/2/3 | {count} |
> | Level IV | {count} |
> | Unknown | {count} |
>
> ⚠️ Surrogate outcome flag set on {surrogate_flagged} articles (primary outcomes differ from PICO-specified outcomes).

Then automatically proceed to Stage 15.

---

## Checkpoint 6: Iteration Decision

Read `reviews/{SLUG}/gap_analysis.json` to get the gap analysis recommendation before presenting this checkpoint.

> "**Phase 4 (Enrichment + Quality) complete.** Here is your updated PRISMA flow:
>
> - Identified: {prisma_counts.identified}
> - Screened: {prisma_counts.screened}
> - Excluded at screening: {sum(excluded_screening.values())}
> - Reports sought for full-text: {prisma_counts.eligibility} (assessed: {prisma_counts.fulltext_assessed}, not retrieved: {prisma_counts.not_retrieved})
> - Added to eligibility via citation searching: {prisma_counts.citation_chasing_added}
> - **Total included (both streams, post-full-text): {prisma_counts.included}**
>
> Evidence level distribution:
> {by_level — show counts}
>
> **Gap analysis recommendation: {recommendation.upper()}**
> {recommendation_rationale}
> PICO gaps identified: {pico_gaps} | Suggested new queries: {suggested_queries}
>
> Would you like to:
> 1. **Proceed to synthesis** (Phase 5) — recommended if you have ≥5 included articles and adequate coverage
> 2. **Run another search iteration** — if you want to add more articles before synthesizing (max {max_iterations} total)
>
> Note: This review has completed {iterations}/{max_iterations} iterations."

If user chooses to iterate, first run retrieval from gap-suggested queries:
```bash
python3 -m src.skills.iteration_manager --review-dir reviews/{SLUG} --retrieve
```

Read the JSON output.

If `no_new_articles: true`:
> "No new articles found beyond previously-screened set. Proceeding to synthesis."

If `no_new_articles: false`, screen the `articles` candidates using the same batching pattern as Stage 8. Use `pico`, `inclusion_criteria`, `exclusion_criteria`, and `screening_task` from the output. For each article:
```json
{"decisions": [{"article_index": N, "decision": "include|exclude|borderline", "reason": "1-2 sentences", "reason_category": "..."|null}]}
```

After screening all candidates, merge results:
```bash
python3 -m src.skills.iteration_manager --review-dir reviews/{SLUG} --merge --decisions-json '{"decisions": [...]}'
```

Display (counts from the `--merge` output):
> **Iteration {N} complete.**
>
> | | Count |
> |---|---|
> | New articles screened | {new_articles_found} |
> | Advancing to full-text | {advancing_to_fulltext} |
>
> Iteration {N}/{max_iterations} complete.

If `advancing_to_fulltext > 0`, the new candidates re-enter the **full-text pipeline** so they clear
the same two gates (abstract + full text) as the primary set. `--merge` set status to
`citation_chased`. Run, in order:

1. **Stage 9** — regenerate the retrieval list (now includes the new candidates):
   ```bash
   python3 -m src.skills.retrieval_list_generator --review-dir reviews/{SLUG}
   ```
2. **Stage 9.5** — download PDFs for the new candidates (skips PDFs already on disk):
   ```bash
   python3 -m src.skills.pdf_downloader --review-dir reviews/{SLUG}
   ```
3. If any new candidates land in `library_request.csv`, optionally run **Stage 9.6** and pause at
   **Checkpoint 4** to fetch library PDFs — a new candidate with no PDF cannot be full-text assessed
   and correctly stays out of the included set (same rule as the primary set).
4. **Stage 10 (scoped)** — assess ONLY the new iteration candidates and append survivors; the
   approved primary/CC includes are never re-assessed:
   ```bash
   # probe
   python3 -m src.skills.fulltext_assessor --review-dir reviews/{SLUG} --prepare --iteration-only
   # read the `articles` array, assess each against pico/inclusion/exclusion, then:
   python3 -m src.skills.fulltext_assessor --review-dir reviews/{SLUG} --save --iteration-only \
     --input-json '{"decisions": [{"orig_idx": N, "decision": "include|exclude", "rationale": "...", "study_design": "..."}]}'
   ```
   (Iteration includes render as a distinct "iteration searching" stream in the PRISMA flow.)

Then re-run **Stages 11–14** (data_extractor → enricher → quality_appraiser → evidence_classifier —
extraction is additive, so the primary set's fields are preserved) and **Stage 15** (gap_analyzer),
then return to **Checkpoint 6**.

If user chooses to proceed:
> "✓ Proceeding to Phase 5: Synthesis + Output.
>
> **Safe session break point.** Phase 5 will generate the full output package: executive summary, evidence table, PRISMA flow, citations (RIS + BibTeX). End this session when ready; resume with `/lit-review`."

If ending: **→ Run Session Note Procedure before closing.**

If the user continues, proceed to Stage 16.

---

## Stage 15: Gap Analysis

Run after evidence classification (Stage 14). Format context for Claude:
```bash
python3 -m src.skills.gap_analyzer --review-dir reviews/{SLUG} --prepare
```

Read the JSON output. Review the `article_summary`, `pico_text`, `evidence_level_distribution`, `study_design_distribution`, and `recency` fields. Apply the `gap_task` instructions to identify specific, actionable gaps and recommend whether to iterate or stop. Construct the full gap analysis JSON matching the schema in `gap_task`. Then save:
```bash
python3 -m src.skills.gap_analyzer --review-dir reviews/{SLUG} --save --input-json '{FULL_GAP_JSON}'
```

Display:
> **Gap analysis complete.**
>
> - PICO gaps identified: {pico_gaps}
> - Suggested new queries: {suggested_queries}
> - Recommendation: **{recommendation.upper()}**
>
> {recommendation_rationale}

Open `reviews/{SLUG}/gap_analysis.md` for the full evidence map and gap details.

Then reach **Checkpoint 6**.

---

## Stage 16: Synthesis + Output Formatting

### Step 1: Synthesize

**Abstract-only supplement (opt-in, off by default):** before running `--prepare`, probe for articles that cleared Stage 8 screening but never reached `articles_included.json` (no PDF retrieved → no full-text appraisal):

```bash
python3 -m src.skills.synthesizer --review-dir reviews/{SLUG} --count-abstract-only
```

Read `abstract_only_candidate_count` from the JSON output. If the count is `0`, skip the question and run plain `--prepare`. If `> 0`, ask **once**:

> "{count} articles cleared Stage 8 screening but were not retrieved at full text, so they're not in the appraised evidence base. Include an **abstract-only supplementary section** in the executive summary (clearly labeled, not part of the main synthesis)? **(default: no)**"

Default to no. Pass `--include-abstract-supplement` to the next call only if the user explicitly says yes.

Format the evidence base for Claude:
```bash
python3 -m src.skills.synthesizer --review-dir reviews/{SLUG} --prepare [--include-abstract-supplement]
```

Read the JSON output. Check `data_completeness_warning` before proceeding:

If `data_completeness_warning` is `true`, display:
> ⚠️ **Data completeness warning.** {data_completeness.total_missing_authors}/{data_completeness.total} articles are missing `authors` and {data_completeness.total_missing_year}/{data_completeness.total} are missing `year`; {data_completeness.cc_incomplete}/{data_completeness.total_citation_chased} citation-chased articles are missing `study_design` or `quality_score`.
>
> Without complete metadata, inline author-year citations in the synthesis cannot be reliably emitted. Recommended remediation:
> - For missing `authors`/`year`: re-run Stage 12 enrichment (`python3 -m src.skills.enricher --review-dir reviews/{SLUG}`) and citation-chase metadata enrichment (`python3 -m src.skills.citation_chaser --review-dir reviews/{SLUG} --enrich-cc`).
> - For missing `study_design`/`quality_score` on CC articles: re-run Stage 11 (`data_extractor`) and Stage 13 (`quality_appraiser`) on the updated set.
>
> You may proceed with synthesis anyway, but expect inline citations like `Unknown 2019` for affected articles.
>
> Continue (yes / re-run-enrichment-first)?

Pause here and wait for user choice. If they choose "re-run-enrichment-first", route them to Stage 12 (or Stage 11/13 as appropriate). If "yes", proceed.

The `synthesis_context` field contains all included articles formatted with citation, design, intervention, outcomes, effect sizes, conclusions, and quality notes. The `pico_outcomes` field lists the PICO outcomes to synthesize against. Apply the `synthesis_task` instructions to produce a narrative synthesis.

**Citation requirement:** Every claim in each `narrative` paragraph must include an author-year inline citation (e.g., Smith 2022, Jones 2023). Do not write a sentence asserting an outcome or finding without citing the source article. If a claim is supported by multiple articles, list all: "(Balasubramanian 2025, Bilicki 2024)". For each claim, verify the cited article's `primary_outcomes` or `author_conclusions` fields (visible in `synthesis_context`) contain content that supports the claim. If an article's metadata fields are empty, do not cite it for a specific finding.

**If `abstract_supplement` is present in the prepare output** (only when the user opted in at the top of this step), follow the `synthesis_task` instructions: do **not** fold those articles into `by_outcome`, `overall_narrative`, `key_findings`, `evidence_quality_summary`, or any inline citation in the main narrative. Instead, add a top-level `abstract_supplement_summary` field (1-2 paragraphs) describing them as supplemental, non-appraised evidence with explicit caveats. Use author-year citations for those articles only inside `abstract_supplement_summary`.

Construct:

```json
{"by_outcome": [{"outcome": "<PICO outcome>", "narrative": "<paragraph>", "highest_level": "<NHMRC level>", "article_count": <int>}], "overall_narrative": "<2-3 paragraph summary>", "key_findings": ["<bullet>"], "evidence_quality_summary": "<paragraph>", "limitations": ["<limitation>"], "abstract_supplement_summary": "<1-2 paragraphs, only if abstract_supplement was provided>"}
```

Then save:
```bash
python3 -m src.skills.synthesizer --review-dir reviews/{SLUG} --save --input-json '{SYNTHESIS_JSON}'
```

Display:
> **Synthesis complete.**
>
> - Articles synthesized: {articles_synthesized}
> - PICO outcomes addressed: {outcomes_addressed}
> - Key findings: {key_findings}

**Stage 16 Step 1 complete.** Before proceeding:
1. Review the key findings above. Does the synthesis look correct and complete?
2. **Context check:** Synthesis generates substantial context. If this session is long, consider ending here and resuming for Step 2 in a new session.

> "Synthesis complete. Would you like to continue to Step 2 (Output Formatting), or end this session and resume later?"

Pause here. Wait for user confirmation before proceeding to Step 2.

### Step 2: Format All Outputs

Format synthesis context for executive summary generation:
```bash
python3 -m src.skills.output_formatter --review-dir reviews/{SLUG} --prepare
```

Read the JSON output. Using `review_question`, `pico_text`, `search_summary`, `key_findings`, `by_outcome`, `evidence_quality`, and `limitations`, write a complete executive summary following the `executive_summary_task` instructions.

**Citation requirement:** Every key finding stated in the executive summary must carry an inline author-year citation. The `by_outcome` narratives from synthesis already contain citations — preserve and use them when drafting the summary. Do not introduce unsupported claims.

Construct:

```json
{"executive_summary": "<full markdown executive summary>"}
```

Then save (this also writes PRISMA flow, RIS, BibTeX, all remaining artifacts, and — via
`--runlog` — the RUNLOG.md `[COMPLETE]` block so closeout cannot be skipped):
```bash
python3 -m src.skills.output_formatter --review-dir reviews/{SLUG} --save --runlog --input-json '{"executive_summary": "{EXECUTIVE_SUMMARY_MARKDOWN}"}'
```
The save output includes `runlog_written: <path>` confirming the completion entry was written.

Display:
> **Output package complete.**
>
> | Artifact | File |
> |---|---|
> | Executive summary | `reviews/{SLUG}/executive_summary.md` |
> | Evidence table | `reviews/{SLUG}/evidence_table.csv` |
> | PRISMA flow | `reviews/{SLUG}/prisma_flow.md` |
> | RIS citations | `reviews/{SLUG}/citations.ris` |
> | BibTeX citations | `reviews/{SLUG}/citations.bib` |
> | Search log | `reviews/{SLUG}/search_log.md` |
> | Retrieval list | `reviews/{SLUG}/retrieval_list.csv` |
> | Gap analysis | `reviews/{SLUG}/gap_analysis.md` |
> | Protocol | `reviews/{SLUG}/protocol.md` |
> | AI disclosure | `reviews/{SLUG}/ai_disclosure.md` |
>
> **Review complete. ✓**
>
> - Total included: {total_included} articles
> - Executive summary: {executive_summary_words} words

If any artifacts are missing:
> ⚠️ Missing: {artifacts_missing}. Check that all prior stages completed successfully.

After status=`complete`, present the final summary:

> "**Review complete.** Here is what was produced:
>
> **To import citations into Zotero:** File → Import → `reviews/{SLUG}/citations.ris`
>
> **For C-suite presentation:** Use `executive_summary.md` (copy-paste into PowerPoint or share as PDF).
>
> **For a polished shareable PDF + DOCX:** Run `/lit-review-package {SLUG}` to bundle everything into a single document.
>
> **For quality committee:** Attach `evidence_table.csv` + `prisma_flow.md`.
>
> Is there anything else you'd like to refine — additional search iteration, narrative adjustments, or a different output format?"

Then run the **Log Run** procedure (see below).

---

## Session Note Procedure

Run at every safe session break point (Checkpoints 1–6). Appends one line to `RUNLOG.md`.

**Step 1:** Ask the user:
> "Any issues to note before ending this session? I'll capture them in the session notes below. Or say 'no issues' to skip."

**Step 2:** Read `state.json` to get `review_id`, `status`, and `raw_question`. Get today's date.

**Step 3:** Append to `RUNLOG.md`:
- If no heading for this review exists yet, insert a new block above `<!-- New entries go here -->`:
  ```
  ## {review_id}

  **Question:** {raw_question}
  **Status:** IN PROGRESS

  ### Session notes
  ```
- Then append under `### Session notes`:
  `- [{YYYY-MM-DD}] Stage: {status} — {user notes or "no issues"}`

**Step 4:** If issues were noted and look like a genuine bug or gap (not just a note-to-self),
tell the user it's captured in the session notes above and suggest they file it as a GitHub issue
in the repo if they'd like it tracked long-term.

---

## Log Run Procedure

Run after `status=complete`. The Stage 16 closeout `--save --runlog` (above) has **already** written
the `## {YYYY-MM-DD} — {review_id} [COMPLETE]` block to `RUNLOG.md` — idempotently replacing any IN
PROGRESS block and preserving its `### Session notes`. This procedure only refines the issues/notes.

**Step 1:** Ask:
> "Any final issues to note for this review?"

**Step 2:** If the user reported issues or notes, re-run the completion writer with them (idempotent —
it replaces the existing `[COMPLETE]` block in place, preserving session notes):
```bash
python3 -m src.skills.output_formatter --review-dir reviews/{SLUG} --save --runlog \
  --runlog-issues '{free-text issue summary, or "none"}' \
  --runlog-notes '{any free-text notes from user}' \
  --input-json '{"executive_summary": "{EXECUTIVE_SUMMARY_MARKDOWN}"}'
```
If there were no issues/notes, the baseline block from Stage 16 is already correct — just verify it
reads `[COMPLETE]` and move on.

**Step 3:** If final issues were noted and look like a genuine bug or gap, suggest the user file it
as a GitHub issue in the repo — it's already captured in `RUNLOG.md`'s session notes either way.

---

## Error Handling

- If any script exits with code 2 (protocol_locked): explain the lock, do not re-run.
- If any script exits with code 1 (general error): show the error message; check that NCBI_API_KEY is set in environment.
- If `state.json` is missing or corrupt: tell the user to check `reviews/{SLUG}/` and offer to re-initialize if the review is new.

---

## Environment Requirements

```
NCBI_API_KEY             — required for Stage 4 (query_builder MeSH lookup) and Stage 5 (PubMed retrieval)
OPENALEX_EMAIL           — recommended for Stage 5 (polite pool; higher rate limits)
CROSSREF_MAILTO          — recommended for Stage 6 (polite pool)
S2_API_KEY               — optional for Stage 5 (Semantic Scholar; higher rate limit without key)
```

All LLM work runs in the Claude Code conversation (Claude.ai Max subscription). No ANTHROPIC_API_KEY needed.

The agent assumes it is run from the `lit-review-agent/` project root (where `src/` and `reviews/` live).

---

## Session Health

**After every stage completes, pause and ask:** "Stage N complete. Would you like to continue to Stage N+1, or save your progress and resume in a new session?"

- **Stage 8 (Abstract Screening):** Parallel mode (default) is one orchestration turn — no mid-stage context risk. The serial fallback runs only when `pending_articles < 50`, so a single batch covers it. No periodic warning needed.
- **Stage 8.5 (Citation Chasing):** Parallel mode is one orchestration turn. The serial fallback runs only when `total_candidates < 50`. No periodic warning needed.
- **Stage 11 (Data Extraction):** Parallel mode is one orchestration turn. The serial fallback runs only when `total_articles < 50`, so a single batch covers it. No periodic warning needed.
- **Stage 13 (Quality Appraisal):** Parallel mode is one orchestration turn. The serial fallback runs only when `total_articles < 50`, so a single batch covers it. No periodic warning needed.

Never silently proceed to the next stage. Every stage transition requires explicit user confirmation.
