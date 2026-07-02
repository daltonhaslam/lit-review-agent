# Lit Review Agent — Project Context
_Read this at the start of every Claude Code session in this project._

---

## What This Project Is

A Claude Code skill (`/lit-review`) that performs rigorous academic literature reviews. It replicates the rigor of a professional systematic reviewer: PICO formulation, multi-database search, PRISMA-grade screening, quality appraisal, and structured synthesis.

**Primary output:** An executive summary + evidence table + Zotero-importable citations, suitable for research summaries and evidence reports.

---

## How the Agent Works

1. User invokes `/lit-review` in Claude Code
2. Agent scans `reviews/` for in-progress reviews (`state.json`); offers to resume or start new
3. Claude orchestrates conversational stages; calls Python scripts via Bash for API calls and processing
4. All outputs written to `reviews/{review-slug}/` after every stage
5. 6 human checkpoints = 6 natural session break points

**Session resume:** Every session begins by running `python src/agent/state.py --scan reviews/`

---

## Key Architecture

**This is the subscription-based version.** All LLM work runs in the Claude Code conversation (covered by Claude.ai Max subscription). Python is a pure data layer — no Anthropic SDK calls.

- **Skill file:** `skills/lit-review.md` → deployed to `~/.claude/skills/lit-review/SKILL.md` (run `make deploy`)
- **Backend:** `src/skills/*.py` — 18 skill modules (one per stage)
- **Pattern:** Each LLM skill has `--prepare` (formats data for Claude) and `--save` (writes Claude's output to disk)
- **APIs:** PubMed, OpenAlex, CrossRef, Semantic Scholar, Europe PMC (no Anthropic API)
- **State:** `reviews/{slug}/state.json` — authoritative; load at session start
- **LLMs:** All run in the Claude Code conversation (subscription); no API key required

Full architecture: `docs/architecture.md`

---

## Where Things Live

| What | Where |
|---|---|
| User guide (how to run the agent) | `README.md` |
| Architecture document | `docs/architecture.md` |
| Folder structure | `docs/folder_structure.md` |
| API guide | `docs/api_guide.md` |
| Evidence levels reference | `docs/evidence_reference.md` |
| Review type decision guide | `docs/review_types.md` |
| API keys (not committed) | `config/api_keys.yaml` or env vars |
| Completed reviews | `reviews/{slug}/` |

---

## Key Design Constraints

- **State-driven:** Never rely on conversation history; always load `state.json`
- **Session-safe:** Every human checkpoint = safe to end session and resume later
- **Token efficiency:** Batch 20 articles per conversation turn for screening; load only current-stage data
- **Academic rigor:** Dual-pass screening, protocol locked before search, PRISMA tracking, citation chasing, quality appraisal per study type
- **No PHI:** Reviews are research questions, not patient data

---

## Academic Foundation

This agent's methodology is grounded in three published sources:
- **Considine et al. (2017):** PICO, Fundamental Four, NHMRC levels, PRISMA, dual-reviewer principle
- **Paré & Kitsiou (2015):** Review typology, six-step method, coverage strategies, PROSPERO
- **Blümle et al. (2019):** PubMed MeSH + free-text strategy, stepwise query history, ATM limitations

---

## Audience

General-purpose systematic and scoping literature reviews. Outputs are suited to research summaries and evidence reports.
