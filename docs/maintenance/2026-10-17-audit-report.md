# Maintenance Audit — 2026-10-17

Branch `claude/maintenance-2026-10`. Scope: docs vs. code, skills vs. pipeline, Semantic
Scholar key path, dependency hygiene, tests, and GitHub housekeeping. No features added. The
skill copies deployed under `~/.claude/skills/` were **not** touched (no `make deploy`).

## Code bugs fixed (each has a failing test first)

| Bug | Impact | Fix |
|---|---|---|
| `iteration_manager --retrieve` called `cfg.get()` on the `APIConfig` dataclass | Checkpoint 6 iteration and every `/lit-review-update` run crashed with `AttributeError` | Read `cfg.ncbi_api_key` |
| `iteration_manager --merge` set `citation_chased` even when nothing advanced | The review was stranded: Stage 16 requires `gap_analyzed` | Stay at `gap_analyzed` when 0 advance |
| `ai_disclosure.md` said "~20 articles per turn", always listed Europe PMC, and gave a 3-step PDF waterfall | The published methods disclosure was inaccurate | Batch sizes come from the `BATCH_SIZE_DEFAULT` constants; Stage 5 names the sources actually searched; the full 6-step waterfall is listed |
| `packager --scan` returned `review_id` only | The update and package skills couldn't find a review whose folder name differs from its `review_id` | Rows now include `slug` (the folder name) |

## Docs/skills vs. code mismatches

| Area | Mismatch (doc said → code does) | Resolution |
|---|---|---|
| Batching | 20 per batch plus periodic Session Health checks → 50 per batch in parallel sub-agent slices | Fixed in architecture, performance_reference, CLAUDE.md |
| `screening_mode`, `settings.yaml` | Fast/thorough changes behavior; settings are loaded → it is a label only; `settings.yaml` is never loaded | Docs relabeled; yaml marked reference-only |
| `review_type` routing | Type changes sources, appraisal, AMSTAR filter → no stage reads it | Routing tables marked advisory |
| GRADE | Applied by `quality_appraiser` → not implemented | Marked reference-only |
| Instrument guide | mixed methods → COREQ + STROBE; no SANRA/Expert Opinion rows → STROBE fall-through; SANRA and Expert Opinion exist; scoping → AMSTAR-2 | Guide matches `detect_instrument` |
| NHMRC levels | Docs give the NHMRC 2009 table → the classifier maps retrospective cohort and case-control to III-3, and ITS/CBA to III-1 | Reference table kept; implementation table added; **deviation left for maintainer** |
| Retrieval sources | CrossRef is a search source; "all 4 databases" → it searches 3 by default (Europe PMC opt-in); CrossRef does DOI resolution and reference lists | Fixed (README, folder_structure, api_guide) |
| Misc. docs | Skill path, state schema nesting, artifact counts, S2 batch size (500 → 100 per call), `REPORT_AUTHOR` env var, `python` → `python3`, surrogate-flag owner, PRISMA layout, session table, model/cost figures | Fixed or labeled historical |
| `/lit-review` | Protocol "locks on approval" (it locks at `--save`); seeds are includes only (they include borderlines); display keys `dois_resolved`/`total_included` don't exist; zero citation candidates may mean an outage; `--sources` tokens; year flags don't set `date_range`; `pubmed_steps` schema; NCBI key "required" | Skill text fixed |
| `/lit-review-quick` | `output_formatter --save '{}'` wrote a 0-byte summary; synthesis JSON schema was wrong; artifact count; "identified" is post-dedup; date range never passed | Skill text fixed |
| `/lit-review-update` | `state.py --scan` never lists complete reviews; `'{}'` wiped the summary; early exits left the review "in progress" with a stub gap analysis; claimed OpenAlex/S2 re-search; `--batch-offset` on `--save` ignored | Skill text fixed (backup/restore, `packager --scan`, `--offset`) |
| `/lit-review-package` | Quick-review detection row; unmatched-citation wording; filenames assume folder = `review_id` | Skill text fixed |

**Dry runs** were done against copies of the demo review with the network blocked. Every
command's flags match its argparse, with no argparse errors. The only non-network failures
were the bugs above.

## Left for the maintainer (see `KNOWN_ISSUES.md`)

- The NHMRC mapping deviation is a methodology call.
- `review_type`, `screening_mode` and `settings.yaml`: wire them up or remove them.
- Best-effort stages (citation chasing, enrichment, PDF download) still treat outages as
  "nothing found".
- `date_range` is never set.
- Updates search PubMed only.
- The packager's quick-review detector checks `review_type == narrative`.
- PubMed partial-page loss; no per-source deadline.
- Iteration and update records don't increment `prisma_counts.identified`.
- The tracked demo review's `ai_disclosure.md`/`prisma_flow.md` predate the wording fixes.
  They were not regenerated.

## Semantic Scholar key path

The key is verified sent as `x-api-key` on search and on batch lookup, including from
`api_keys.yaml` through the retriever. A blank key sends no header. A persistent
unauthenticated 429 reaches `search_log.md` and the packaged report's Methods section,
together with the key hint. The README instructions match `api_keys.yaml.template` (same
URL, key name, and env var). The new tests are in `tests/test_semantic_scholar_key_path.py`
(`httpx.MockTransport`).

## Dependencies (fresh Python 3.11 venv)

`pip-audit` found 89 advisories in 3 packages. All are fixed only in a **major** version, so
nothing was bumped:

| Package | Pinned | Advisories | Fixed in | Note |
|---|---|---|---|---|
| pypdf | `<6` (5.9.0) | 85 (mostly malformed-PDF parsing) | 6.x | Parses downloaded PDFs — **highest priority** |
| lxml | `<6` (5.4.0) | 1 (XXE in `iterparse` defaults) | 6.1 | **Not imported anywhere** (XML goes through defusedxml) |
| pytest | `<9` (8.4.2) | 1 (shared `/tmp` dir) | 9.0.3 | Dev-only |

No patch bumps were available: the ranges already resolve to the newest in-range releases.
The suite passes unchanged on pypdf 6.21, lxml 6.1, pytest 9.1 and pytest-asyncio 1.4. Also
unused: `aiohttp` and `python-dateutil`. Recommendation: bump pypdf to `<7`, drop lxml,
aiohttp and dateutil.

## Tests

| | Before | After |
|---|---|---|
| Result | 1080 passed, plus 6 live tests collected | **1093 passed, 6 skipped** (network-marked) |
| Time | ~19 s | ~4 s |
| Network | 17 tests silently hit live APIs; 6 connectivity tests ran live | `tests/conftest.py` blocks sockets; 0 tests reach the network even with the guard off |
| Python 3.9 | not checked | passes |

No flakes across repeated runs. 13 tests were added. `test_api_connectivity.py` is marked
`network` and runs only with `LIT_REVIEW_NETWORK_TESTS=1`.

## GitHub housekeeping

No open issues or PRs. The issue and PR templates match CONTRIBUTING (same test command and
checklist). `CITATION.cff` version 1.0.0 matches tag `v1.0.0`. The README states no version.
The commits since v1.0.0 (including this branch) are unreleased. No tag was created.

**Process:** `/simplify`, then `/code-review` (10 findings, all fixed or answered), then
`/security-review` (no findings), then a re-review of the fixes (9 findings). 7 of those were
fixed or recorded in KNOWN_ISSUES. One was a false positive: the global `asyncio.sleep`
patch matches the earlier "scoped" one. The last is out of scope: the deployed
`~/.claude/skills/*/SKILL.md` copies don't have these skill fixes yet.
