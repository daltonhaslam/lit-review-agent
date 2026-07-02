---
name: lit-review-update
description: Use when the user invokes /lit-review-update to update a completed systematic review with new literature published since the original search date. Re-runs retrieval, screens only new articles, and merges new includes into existing outputs.
---

# Lit Review Update — Skill

Update an existing **completed** systematic review with new literature published since the original search date. Re-runs retrieval using the original queries, screens only new articles (not previously screened), and merges new includes into the review outputs.

---

## On Activation

**Always start here:**

```bash
python src/agent/state.py --scan reviews/
```

Present any completed reviews (`status=complete`). Ask:
> "Which completed review would you like to update? I'll re-run the original search queries and screen only new articles."

If no completed reviews are found, offer to start a new review with `/lit-review` instead.

After the user selects a review, load its state and proceed to the Update Workflow below.

---

## Update Workflow

### Step 1: Confirm Scope

Display the review summary:
> "**Review:** {raw_question}
> **Original search date:** {created_at}
> **Included articles:** {prisma_counts.included}
> **Original queries:** {queries.pubmed[-1].query_string} (PubMed), plus OpenAlex and Semantic Scholar
>
> I will re-run these queries with a date filter from {created_at.year} to today, then screen only articles not in the existing screened set ({prisma_counts.screened} articles already seen).
>
> Proceed?"

### Step 2: Reset State for Update

Set status back to `gap_analyzed` so `iteration_manager` can run:

```bash
python - <<'EOF'
import sys; sys.path.insert(0, ".")
from pathlib import Path
from src.agent import state as sm
from src.models.review import ReviewStatus

review_dir = Path("reviews/{SLUG}")
r = sm.load(review_dir)
r.status = ReviewStatus.gap_analyzed
r.max_iterations = r.iterations + 1  # allow one more iteration
sm.save(r, review_dir)
print(f"Status reset. iterations={r.iterations}, max_iterations={r.max_iterations}")
EOF
```

### Step 3: Generate Update Queries

Build a date-filtered version of the existing queries. Take the last PubMed query from state and append a date filter:

```bash
python - <<'EOF'
import sys, json; sys.path.insert(0, ".")
from pathlib import Path
from src.agent import state as sm
from src.models.review import ReviewStatus

review_dir = Path("reviews/{SLUG}")
r = sm.load(review_dir)

# Build date-filtered query
original_query = r.queries.pubmed[-1].query_string if r.queries.pubmed else ""
search_year = r.created_at.year
from datetime import datetime
current_year = datetime.utcnow().year
date_filtered = f"({original_query}) AND {search_year}:{current_year}[dp]"

# Write as gap_analysis.json with one "suggested query"
gap_data = {
    "pico_gaps": [],
    "suggested_queries": [
        {
            "target_gap": "new literature since original search",
            "query": date_filtered,
            "rationale": f"Re-run original query filtered to {search_year}–{current_year}"
        }
    ],
    "recommendation": "iterate",
    "recommendation_rationale": "Update run triggered manually."
}
(review_dir / "gap_analysis.json").write_text(json.dumps(gap_data, indent=2))
# iteration_manager --retrieve requires status gap_analyzed; the review is currently `complete`.
# Reset the status pointer (the artifacts on disk are unchanged) so the update iteration can run.
r.status = ReviewStatus.gap_analyzed
sm.save(r, review_dir)
print(json.dumps({"date_filtered_query": date_filtered}))
EOF
```

Confirm with the user:
> "Update query: `{date_filtered_query}`
>
> This will search for articles published between {original_year} and today. Proceed?"

### Step 4: Run Iteration (retrieve → screen → merge)

`iteration_manager` is a two-invocation contract (`--retrieve` then `--merge`) with a screening
step in between — a bare invocation exits with "one of the arguments --retrieve --merge is
required", and `--retrieve` alone never produces `new_includes`. Mirror `/lit-review` Checkpoint 6.

**(a)** Retrieve the date-filtered new literature:
```bash
python -m src.skills.iteration_manager --review-dir reviews/{SLUG} --retrieve
```
Read the JSON output.

If `no_new_articles: true`:
> "No new articles were found since the original search. The review is up to date."
> Done — no further action needed.

**(b)** If `no_new_articles: false`, screen the `articles` candidates against the returned `pico`,
`inclusion_criteria`, `exclusion_criteria`, and `screening_task` (same batching as Stage 8), producing:
```json
{"decisions": [{"article_index": N, "decision": "include|exclude|borderline", "reason": "1-2 sentences", "reason_category": "..."|null}]}
```

**(c)** Merge the decisions (this appends screened articles, flags new includes as an iteration
stream, and advances status to `fulltext_assessed`):
```bash
python -m src.skills.iteration_manager --review-dir reviews/{SLUG} --merge --decisions-json '{"decisions": [...]}'
```

Display (counts come from the `--merge` output):
> **Update retrieval complete.**
>
> | | Count |
> |---|---|
> | New articles found | {new_articles_found} |
> | New includes added | {new_includes} |
> | Iteration | {iteration} |

If `advancing_to_fulltext == 0`:
> "No new articles were found that meet your inclusion criteria. The review is up to date."
> Done — no further action needed.

If `advancing_to_fulltext > 0`, continue to Step 5.

### Step 5: Full-text assess + process new candidates

`--merge` set status to `citation_chased`. The new candidates must clear **full-text assessment**
(the same second gate as the original review) before they count as included. First run the scoped
full-text sub-flow, then the extraction stages:

1. **Stage 9 → 9.5** — regenerate the retrieval list and download PDFs for the new candidates
   (idempotent; skips PDFs already on disk):
   ```bash
   python -m src.skills.retrieval_list_generator --review-dir reviews/{SLUG}
   python -m src.skills.pdf_downloader --review-dir reviews/{SLUG}
   ```
   (Candidates that land in `library_request.csv` with no PDF cannot be full-text assessed and stay
   out of the included set — same rule as the original review.)
2. **Stage 10 (scoped)** — assess ONLY the new candidates and append survivors (never re-assess the
   original included set):
   ```bash
   python -m src.skills.fulltext_assessor --review-dir reviews/{SLUG} --prepare --iteration-only
   # assess each returned article, then:
   python -m src.skills.fulltext_assessor --review-dir reviews/{SLUG} --save --iteration-only \
     --input-json '{"decisions": [{"orig_idx": N, "decision": "include|exclude", "rationale": "...", "study_design": "..."}]}'
   ```
3. Then re-run **Stages 11–16** (data_extractor → enricher → quality_appraiser → evidence_classifier
   → gap_analyzer → synthesizer → output_formatter). Extraction is additive, so the original
   articles' data is preserved.

> "{advancing_to_fulltext} new candidates found. Running full-text assessment, then data extraction, enrichment, quality appraisal, evidence classification, gap analysis, synthesis, and output regeneration..."

**Stage 11 (data_extractor):** mirror `/lit-review` Stage 11 serial mode (see the "Serial mode" subsection under "Stage 11: Data Extraction" in `skills/lit-review.md`):
```bash
python3 -m src.skills.data_extractor --review-dir reviews/{SLUG} --prepare \
  --batch-size 50 --batch-offset 0
# (Read articles array, apply extraction_task, build {EXTRACTIONS_JSON})
python3 -m src.skills.data_extractor --review-dir reviews/{SLUG} --save \
  --batch-size 50 --offset 0 --input-json '{EXTRACTIONS_JSON}'
```
Advance `--batch-offset` by 50 until `"complete": true`.

**Stage 12 (enricher):** pure-data module, no `--prepare`/`--save` needed:
```bash
python3 -m src.skills.enricher --review-dir reviews/{SLUG}
```

**Stage 13 (quality_appraiser):** mirror `/lit-review` Stage 13 serial mode:
```bash
python3 -m src.skills.quality_appraiser --review-dir reviews/{SLUG} --prepare \
  --batch-size 50 --batch-offset 0
# (Read articles array, apply appraisal_task, build {APPRAISALS_JSON})
python3 -m src.skills.quality_appraiser --review-dir reviews/{SLUG} --save \
  --batch-size 50 --batch-offset 0 --input-json '{APPRAISALS_JSON}'
```
Advance `--batch-offset` by 50 until `"complete": true`.

**Stage 14 (evidence_classifier):** pure-data module:
```bash
python3 -m src.skills.evidence_classifier --review-dir reviews/{SLUG}
```

**Stage 15 (gap_analyzer):** mirror `/lit-review` Stage 15:
```bash
python3 -m src.skills.gap_analyzer --review-dir reviews/{SLUG} --prepare
# (Read article_summary, pico_text, evidence_level_distribution, study_design_distribution, recency. Apply gap_task to build {GAP_JSON})
python3 -m src.skills.gap_analyzer --review-dir reviews/{SLUG} --save \
  --input-json '{GAP_JSON}'
```

**Stage 16 Step 1 (synthesizer):**
```bash
python3 -m src.skills.synthesizer --review-dir reviews/{SLUG} --prepare
# (Read synthesis_context, apply synthesis_task to build {SYNTHESIS_JSON})
python3 -m src.skills.synthesizer --review-dir reviews/{SLUG} --save \
  --input-json '{SYNTHESIS_JSON}'
```

**Stage 16 Step 2 (output_formatter):**
```bash
python3 -m src.skills.output_formatter --review-dir reviews/{SLUG} --prepare
python3 -m src.skills.output_formatter --review-dir reviews/{SLUG} --save \
  --input-json '{}'
```

(Note: no `citation_chased` status reset is needed — `evidence_classified` is the correct upstream status for `gap_analyzer`.)

### Step 6: Confirm Completion

> "**Update complete.** The review now includes {total_included} articles (previously {original_included}, {new_includes} added).
>
> Updated artifacts:
> - `reviews/{SLUG}/executive_summary.md` — updated narrative
> - `reviews/{SLUG}/evidence_table.csv` — includes new articles
> - `reviews/{SLUG}/citations.ris` — updated Zotero import
>
> To import updated citations into Zotero: File → Import → `reviews/{SLUG}/citations.ris`"

---

## Error Handling

- If `gap_analysis.json` cannot be written (permissions): check that `reviews/{SLUG}/` is writable.
- If `iteration_manager` returns `new_articles_found=0`: the review is current — no further action needed.
- If the original queries are missing from state: reconstruct from `protocol.md` or `search_log.md`, then manually populate `gap_analysis.json`.

---

## Environment Requirements

Same as `/lit-review`: `NCBI_API_KEY`, `OPENALEX_EMAIL`, `SEMANTIC_SCHOLAR_API_KEY` (optional but recommended). No `ANTHROPIC_API_KEY` required — subscription mode runs all LLM stages in the Claude Code conversation.
