---
name: lit-review-quick
description: Use when the user invokes /lit-review-quick for a rapid scoping evidence scan. Skips full-text eligibility, quality appraisal, and citation chasing. Produces narrative summary + evidence map from title/abstract screening only.
---

# Lit Review Quick — Scoping Review Fast Path

Rapid evidence scan for scoping reviews and landscape assessments. Skips full-text eligibility, quality appraisal, and citation chasing. Produces a narrative summary + evidence map from title/abstract screening only.

**Use when:** You need a rapid landscape of the evidence, not a definitive systematic answer. Suitable for exploratory questions, background preparation, and C-suite briefings where precision < speed.

**Not appropriate for:** Clinical guideline development, GRADE evidence grading, policy decisions requiring systematic rigor.

---

## On Activation

**Always start here:**

```bash
python src/agent/state.py --scan reviews/
```

Present in-progress reviews (if any); offer to resume or start new. If starting new, proceed to Quick Initialization.

---

## Quick Initialization

```bash
python - <<'EOF'
import sys, json
from pathlib import Path
sys.path.insert(0, ".")
from src.agent import state as sm
from src.models.review import Review, ReviewType, ScreeningMode

review_dir = Path("reviews/{SLUG}")
review = Review(
    review_id="{SLUG}",
    raw_question="{QUESTION}",
    review_type=ReviewType.scoping,
    screening_mode=ScreeningMode.fast,
)
sm.save(review, review_dir)
print(json.dumps({"review_id": review.review_id}))
EOF
```

Confirm:
> "Quick scoping review initialized. This fast path skips full-text eligibility and quality appraisal — ideal for rapid landscape mapping. Starting Stage Q1: PICO formulation."

---

## Stage Q1: PICO (Abbreviated)

Ask three questions (not four — skip Fundamental Four):

1. "What is your question — what population, intervention/exposure, and outcome(s)?"
2. "Any date range or study design preference? (default: last 10 years, all designs)"
3. "How many articles would be useful? (default: top 100 per source)"

Then format context for Claude (`--prepare`), build the PICO, and save it (`--save`) — the
two-call pattern; a bare invocation exits with "one of the arguments --prepare --save is required":
```bash
python -m src.skills.pico_refiner \
  --review-dir reviews/{SLUG} \
  --answers-json '{"what_we_know": "", "what_we_dont_know": "{QUESTION}", "what_we_should_know": "{QUESTION}", "why_we_should_know": "rapid landscape"}' \
  --prepare
```
Read the JSON output and build the full PICO(S) framework, then save:
```bash
python -m src.skills.pico_refiner --review-dir reviews/{SLUG} --save --input-json '{FULL_PICO_JSON}'
```

Present PICO; ask for confirmation. No protocol generation step.

After PICO confirmed, proceed to Stage Q2.

---

## Stage Q2: Query + Retrieval

Build queries (two-call `--prepare`/`--save`; a bare invocation exits with
"one of the arguments --prepare --save is required"):

```bash
python -m src.skills.query_builder --review-dir reviews/{SLUG} --prepare
```
Read the JSON output, build the per-source queries, then save:
```bash
python -m src.skills.query_builder --review-dir reviews/{SLUG} --save \
  --input-json '{"pubmed_steps": [...], "openalex_query": "...", "semantic_scholar_query": "...", "europepmc_query": "..."}'
```

Then retrieve:

```bash
python -m src.skills.multi_source_retriever \
  --review-dir reviews/{SLUG} \
  --sources pubmed,openalex,semantic_scholar \
  --max-per-source 150
```

Followed by aggregation and deduplication:

```bash
python -m src.skills.aggregator --review-dir reviews/{SLUG}
python -m src.skills.deduplicator --review-dir reviews/{SLUG}
```

Display PRISMA identified count.

---

## Stage Q3: Fast Screening

Screen abstracts in batches of 20. Get the first batch:

```bash
python -m src.skills.abstract_screener --review-dir reviews/{SLUG} --prepare \
  --batch-size 20 --batch-offset 0
```

**REPEAT — advance `CURRENT_OFFSET` by 20 each iteration, starting at 0:**

**Step 1** (skip on first iteration): Get next batch:
```bash
python -m src.skills.abstract_screener --review-dir reviews/{SLUG} --prepare \
  --batch-size 20 --batch-offset {CURRENT_OFFSET}
```

**Step 2** — Screen the batch. For a scoping review, apply the PICO criteria but use a liberal threshold — include borderline articles. Construct:
```json
{"decisions": [{"article_index": N, "decision": "include|exclude|borderline", "reason": "...", "reason_category": "..."|null}]}
```

**Step 3** — Save:
```bash
python -m src.skills.abstract_screener --review-dir reviews/{SLUG} --save \
  --offset {CURRENT_OFFSET} --decisions-json '{DECISIONS_JSON}'
```

If `"complete": true`, stop.

**END REPEAT**

Display screening summary. **No retrieval list step** — scoping reviews do not require full-text for all included articles.

Ask user:
> "Screening complete. {include} articles included, {borderline} borderline. For a scoping review, I'll use abstract-level data only. Proceed to synthesis?"

### Bridge to extraction (scoping path skips full text)

The quick path has no full-text stage, so `articles_included.json` was never created and the status
is still `abstract_screening`. Stage Q4's `data_extractor --prepare` requires status `fulltext_assessed`
and reads `articles_included.json`. Materialize it from the include/borderline screening decisions and
advance the status (abstract-level extraction; no PDFs):

```bash
python - <<'EOF'
import sys, json; sys.path.insert(0, ".")
from pathlib import Path
from src.agent import state as sm
from src.models.review import ReviewStatus
rd = Path("reviews/{SLUG}")
r = sm.load(rd)
screened = json.loads((rd / r.articles_screened_file).read_text())
included = [a for a in screened if a.get("screening_decision") in ("include", "borderline")]
(rd / r.articles_included_file).write_text(json.dumps(included, indent=2))
r.prisma_counts.eligibility = len(included)
r.prisma_counts.included = len(included)
r.status = ReviewStatus.fulltext_assessed
sm.save(r, rd)
print(f"Bridged {len(included)} include/borderline articles to articles_included.json.")
EOF
```

---

## Stage Q4: Lightweight Extraction + Synthesis

Quick path runs serial (article counts are typically small in scoping reviews).

### Step 1 — Data Extraction

Probe the prepare to load the batch:
```bash
python3 -m src.skills.data_extractor --review-dir reviews/{SLUG} --prepare \
  --batch-size 50 --batch-offset 0
```

Read the `articles` array (each has `orig_idx` and `article_text`) and the `extraction_task` text. Apply the extraction_task instructions to produce:
```json
{"extractions": [{"orig_idx": N, "study_design": "...", "sample_size": N|null, "follow_up_duration": "...|null", "intervention_details": "...|null", "comparison_details": "...|null", "primary_outcomes": [...], "effect_sizes": "...|null", "secondary_outcomes": [...], "author_conclusions": "...|null", "funding_source": "...|null", "coi_declared": true|false|null}]}
```

Save the extractions:
```bash
python3 -m src.skills.data_extractor --review-dir reviews/{SLUG} --save \
  --batch-size 50 --offset 0 --input-json '{EXTRACTIONS_JSON}'
```

If `"complete": false`, advance `--batch-offset` by 50 and repeat. Stop when `"complete": true`.

Note: gap_analyzer is skipped in the quick path. Set status to `gap_analyzed` manually before running synthesizer:

```bash
python - <<'EOF'
import sys; sys.path.insert(0, ".")
from pathlib import Path
from src.agent import state as sm
from src.models.review import ReviewStatus
r = sm.load(Path("reviews/{SLUG}"))
r.status = ReviewStatus.gap_analyzed
sm.save(r, Path("reviews/{SLUG}"))
print("Status updated.")
EOF
```

### Step 2 — Synthesis

Probe prepare:
```bash
python3 -m src.skills.synthesizer --review-dir reviews/{SLUG} --prepare
```

Read the `synthesis_context`, `pico_text`, and `synthesis_task` fields. Apply the synthesis_task instructions to construct:
```json
{
  "executive_summary": "...",
  "by_outcome": [{"outcome": "...", "narrative": "..."}],
  "limitations": "...",
  "conclusion": "..."
}
```

Save:
```bash
python3 -m src.skills.synthesizer --review-dir reviews/{SLUG} --save \
  --input-json '{SYNTHESIS_JSON}'
```

---

## Stage Q5: Output

Probe prepare:
```bash
python3 -m src.skills.output_formatter --review-dir reviews/{SLUG} --prepare
```

Read the prepare output. The output_formatter is a pure-data module — it generates artifacts directly. Then save:
```bash
python3 -m src.skills.output_formatter --review-dir reviews/{SLUG} --save \
  --input-json '{}'
```

Display the same artifact list as the full review. Note in the executive summary that this is a **scoping review / rapid landscape** — not a systematic review — so GRADE quality assessment was not performed.

---

## Differences from Full /lit-review

| Feature | /lit-review | /lit-review-quick |
|---|---|---|
| PICO depth | Fundamental Four + full PICO | Abbreviated PICO |
| Protocol | PROSPERO-style, locked | None |
| Screening | Dual-pass Sonnet 4.6 | Haiku fast + Sonnet borderlines |
| Full-text eligibility | Yes | No |
| Quality appraisal | AMSTAR-2 / RoB 2.0 / STROBE / COREQ / SANRA / Expert Opinion | None |
| Evidence classification | NHMRC I–IV | None |
| Citation chasing | Yes | No |
| Gap analysis | Yes | No |
| Output artifacts | 9 | 7 (no quality_report.md, retrieval_list.csv) |
| Typical session count | 4–6 | 1–2 |

---

## Environment Requirements

Same as `/lit-review`:
```
NCBI_API_KEY       — required for PubMed
OPENALEX_EMAIL     — recommended
```

All LLM work runs in the Claude Code conversation (Claude.ai Max subscription). No ANTHROPIC_API_KEY needed.
