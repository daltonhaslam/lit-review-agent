# Lit Review Agent

Academic-physician-grade systematic literature review tool, built as a set of [Claude Code](https://claude.com/claude-code) skills. Given a research question, it runs the full workflow of a professional systematic review — PICO formulation, multi-database search, PRISMA-grade dual-pass screening, citation chasing, quality appraisal, and structured synthesis — and produces an executive summary, evidence table, and Zotero-importable citations suitable for C-suite presentations and quality committee reports. It runs entirely inside your Claude Code subscription; no Anthropic API key is required (the Python backend is a pure data layer that talks to free literature APIs, never the model).

---

## Features

- **PICO(S) formulation** via a structured "Fundamental Four" interview, locked into a PROSPERO-style protocol before search begins
- **Multi-source retrieval** across PubMed, OpenAlex, and Semantic Scholar (Europe PMC optional), with CrossRef for DOI resolution and reference lists, and with a full MeSH + free-text query audit trail
- **PRISMA 2020-compliant pipeline**: deduplication, dual-pass abstract screening, backward/forward citation chasing, full-text eligibility assessment, and a generated PRISMA flow diagram
- **Automated open-access PDF retrieval** (Unpaywall, PubMed Central, Europe PMC, OpenAlex, CrossRef) with a guided manual step for the rest — no paywall bypass
- **Study-type-aware quality appraisal**: AMSTAR-2, Cochrane RoB 2, STROBE, COREQ, SANRA, or an Expert Opinion rubric, chosen automatically per article
- **NHMRC evidence-level classification** (I–IV) and structured gap analysis with an iterate-or-proceed decision point
- **6 human checkpoints** so you can pause and resume across sessions without losing state
- **Zotero-ready output**: RIS + BibTeX citation files alongside the evidence table and narrative synthesis
- **Optional polished export**: bundle a completed review into a single PDF + DOCX deliverable

---

## Install

Python 3.9+ (developed on 3.11; the test suite also passes on macOS system Python 3.9 — no CI enforces either).

```bash
git clone https://github.com/daltonhaslam/lit-review-agent.git
cd lit-review-agent
pip install -r requirements.txt
cp config/api_keys.yaml.template config/api_keys.yaml
# Edit config/api_keys.yaml — see API Keys Setup below
make deploy
```

`make deploy` copies each skill in `skills/` to `~/.claude/skills/{name}/SKILL.md`, the folder structure Claude Code requires to discover a skill. Re-run it any time you pull skill changes.

### API Keys Setup

`config/api_keys.yaml` (gitignored — never commit it) holds:

```yaml
ncbi_api_key: ""              # Free at https://www.ncbi.nlm.nih.gov/account/ (raises PubMed rate limit)
openalex_email: ""            # Any email — joins OpenAlex's polite pool
crossref_mailto: ""           # Any email — joins CrossRef's polite pool
unpaywall_email: ""           # Any email — required by the Unpaywall API for open-access lookups
semantic_scholar_api_key: ""  # Recommended — the unauthenticated pool is often rate-limited (429); free at semanticscholar.org/product/api
report_author: ""             # Your name as it should appear on generated reports (e.g. "Jane Smith, MD"). Blank = "Prepared using lit-review-agent".
```

Every key can also be set as an environment variable: `NCBI_API_KEY`, `OPENALEX_EMAIL`, `CROSSREF_MAILTO`, `UNPAYWALL_EMAIL`, `SEMANTIC_SCHOLAR_API_KEY` (the older `S2_API_KEY` name still works). None of these are required to run a review — they raise rate limits and add polite-pool access, and every source falls back to unauthenticated access.

**Semantic Scholar caveat:** the shared unauthenticated pool answers HTTP 429 for long stretches. Without a key, retrieval retries with backoff and, if the pool stays saturated, reports Semantic Scholar as a *degraded source* in the retrieval summary (`degraded_sources` / `source_status`) and in `search_log.md` rather than recording a silent zero. A free key from https://www.semanticscholar.org/product/api removes the problem.

---

## Usage

Open Claude Code in this project directory and invoke one of:

| Skill | Use case |
|---|---|
| `/lit-review` | Full systematic review — PICO through output package, 6 checkpoints (up to 6 natural session breaks); most reviews take about 4–6 sessions |
| `/lit-review-quick` | Rapid scoping scan — skips full-text eligibility, quality appraisal, and citation chasing; narrative summary + evidence map from abstract screening only |
| `/lit-review-update` | Re-run retrieval on an existing completed review, screen only new articles, and merge new includes into the existing outputs |
| `/lit-review-package` | Bundle a completed review's outputs into a single polished PDF + DOCX deliverable |

The agent asks for your research question and guides you through every stage from there. If you close Claude Code mid-review, just start `/lit-review` again — it scans `reviews/` for in-progress state and offers to resume.

For a worked example of the full output — protocol, evidence table, executive summary, PRISMA flow diagram, gap analysis, and citation files — see [`reviews/demo-chf-care-transitions/`](reviews/demo-chf-care-transitions/).

For guidance on which skill fits your situation, see [docs/review_types.md](docs/review_types.md). For full technical documentation, see [docs/architecture.md](docs/architecture.md).

---

## PDF Retrieval

Full-text PDFs are needed for eligibility assessment (Stage 10) and data extraction (Stage 11). The agent gets you as far as it legitimately can, then hands the rest back to you:

1. **Automated, open-access only.** After screening, the agent queries Unpaywall, PubMed Central, Europe PMC, OpenAlex, and CrossRef for a legally open copy of each included article and downloads what it finds.
2. **Manual, your own access.** For articles with no open-access copy, the agent opens each one's page in your browser so you can download the PDF if you have legitimate access (personal subscription, institutional login already active in your browser, etc.), then imports what landed in your downloads folder.
3. **Library request.** Anything still missing is written to `library_request.csv` — hand this to your own institution's library and request it through your normal channels.

There is no automated paywall-bypass or institutional-proxy integration in this tool — no browser-session replay of any kind. You are responsible for ensuring any full text you obtain is through access you are entitled to.

> **Platform note:** the manual browser-retrieval step (Stage 9.6) and the `make package-deps` helper assume macOS — they use `open -a "Google Chrome"`, `~/Downloads`, and Homebrew. Other platforms will need manual equivalents (e.g. a different browser-launch command, download directory, and package manager).

---

## Pipeline Overview

The full `/lit-review` skill runs a 16-stage pipeline (with sub-stages 8.5, 9.5, and 9.6) through **6 human checkpoints**, each a safe point to pause and resume in a later session:

1. **Question formulation** — review type classification, PICO(S) formulation (Checkpoint 1: approve PICO), protocol generation (Checkpoint 2: approve + lock protocol), query construction
2. **Retrieval & screening** — multi-source retrieval, aggregation, deduplication (Checkpoint 3: review PRISMA identified count), dual-pass abstract screening, citation chasing
3. **PDF acquisition** — automated open-access download, then optional manual browser retrieval (Checkpoint 4: library retrieval break for anything still missing)
4. **Full text & extraction** — full-text eligibility assessment, structured data extraction into `evidence_table.csv` (Checkpoint 5: approve included set)
5. **Quality & classification** — citation-metadata enrichment, per-study quality appraisal, NHMRC evidence-level classification, gap analysis (Checkpoint 6: iterate or proceed)
6. **Synthesis & output** — narrative synthesis and generation of the full output package (executive summary, evidence table, PRISMA flow diagram, gap analysis, citation files)

Every stage writes its output to `reviews/{review-slug}/` immediately, and `state.json` is the authoritative record of where a review stands — nothing depends on conversation history surviving between sessions.

---

## Output Files

| File | Contents |
|---|---|
| `state.json` | Authoritative review state — load this to resume |
| `protocol.md` | Pre-registered protocol (locked before search) |
| `search_log.md` | Full query audit trail with hit counts per source |
| `evidence_table.csv` | Structured data extraction (population, intervention, outcomes, effects) |
| `retrieval_list.csv` | Combined candidate list with PDF status |
| `library_request.csv` | Articles to request through your institution's library |
| `quality_report.md` | Per-article quality appraisal with instrument and score |
| `executive_summary.md` | Academic-physician narrative summary (C-suite format) |
| `prisma_flow.md` | PRISMA 2020 flow diagram with counts |
| `gap_analysis.md` | Evidence gaps and future research directions |
| `ai_disclosure.md` | AI-use disclosure — which stages were AI-assisted vs. automated, human checkpoints, limitations |
| `citations.ris` | Zotero-importable citation file |
| `citations.bib` | BibTeX for academic writing |

---

## Packaging a Completed Review

Once a review reaches `status: complete`:

```
/lit-review-package {SLUG}
```

This bundles the executive summary, methods, by-outcome findings, quality summary, gaps, and full reference list into `reviews/{SLUG}/{SLUG}_report.pdf` and `.docx`. Re-running is idempotent.

One-time setup (Pandoc + xelatex):

```bash
make package-deps
```

---

## Known Issues

Current limitations — Semantic Scholar rate limits without a key, open-access-only retrieval, the NHMRC mapping deviation, and more — are listed in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

---

## Contributing

Contributions — bug fixes, features, documentation, tests — are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, test, and pull request instructions, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community expectations.

---

## Citation

If you use this tool in your work, please cite the repository — see the "Cite this repository" button on GitHub, or [`CITATION.cff`](CITATION.cff) directly.

---

## Acknowledgments

The review methodology is grounded in three academic sources:

- Considine J, Shaban RZ, Fry M, Curtis K. Evidence-based practice: the need for clinical criteria in evaluating literature reviews. *Aust Emerg Nurs J.* 2017 — PICO formulation, the "Fundamental Four," NHMRC evidence levels, PRISMA.
- Paré G, Kitsiou S. Methods for literature reviews. In: *Handbook of eHealth Evaluation.* University of Victoria; 2015 — review typology, six-step method, PROSPERO registration.
- Blümle A, Lagrèze WA, Motschall E. [Systematic literature searches in PubMed: the road to success]. *Ophthalmologe.* 2019 — MeSH + free-text search strategy, stepwise query history.

Quality appraisal instruments: AMSTAR-2 (Shea et al., *BMJ* 2017), Cochrane Risk of Bias 2 (Sterne et al., *BMJ* 2019), STROBE (von Elm et al., *PLoS Med* 2007), COREQ (Tong et al., *Int J Qual Health Care* 2007), and SANRA (Baethge et al., *Res Integr Peer Rev* 2019).

---

## Disclaimer

This tool is a research aid, not a substitute for professional judgment — verify all outputs before relying on them clinically, academically, or administratively. Obtain full-text articles only through legitimate access you are entitled to (open access, personal subscription, or your own institution's library).

---

## License

MIT — see [LICENSE](LICENSE).

Created by Dalton Haslam, MD MBA ([github.com/daltonhaslam](https://github.com/daltonhaslam)).
