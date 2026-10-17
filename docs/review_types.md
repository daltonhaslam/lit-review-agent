# Review Type Decision Guide
_Last updated: April 2026_

Source: Paré & Kitsiou (2015), Handbook of eHealth Evaluation, Ch. 9

---

## Decision Tree: Which Review Type Do You Need?

```
What is your primary goal?
│
├─ "Map what literature exists on a topic before going deeper"
│   → SCOPING REVIEW
│
├─ "Answer a specific clinical or operational question with the strongest evidence"
│   ├─ "Exhaustive, publishable, PRISMA-grade"
│   │   → SYSTEMATIC REVIEW
│   └─ "Quick summary for decision-making (C-suite, staff meeting)"
│       → NARRATIVE REVIEW
│
├─ "Summarize and compare findings from multiple systematic reviews"
│   → UMBRELLA REVIEW (Overview of SRs)
│
├─ "Understand why an intervention works in some contexts but not others"
│   → REALIST REVIEW
│
└─ "Critically evaluate strengths and contradictions in the literature"
    → CRITICAL REVIEW
```

---

## Full Typology

From Paré & Kitsiou (2015). All types follow the same six generic steps, but differ in coverage strategy, rigor requirements, and synthesis method.

### Narrative Review
- **Goal:** Summarize and describe existing literature; build a conceptual framework
- **Coverage:** Selective — author-chosen sources; no requirement for exhaustiveness
- **Appraisal:** None required
- **Synthesis:** Thematic, chronological, or conceptual framework
- **Typical use:** Background sections; conference presentations; conceptual papers
- **Academic physician use:** Staff education; introducing a topic to medical staff leadership
- **Time estimate:** Days to 2 weeks

### Scoping Review
- **Goal:** Map the size and nature of the evidence base; identify key concepts, types of evidence, and gaps; determine whether a full systematic review is warranted
- **Coverage:** Representative → comprehensive; iterative refinement of search; grey literature included
- **Appraisal:** None required (though quality may be noted)
- **Synthesis:** Analytic/thematic frameworks + numerical distributions and frequency tables
- **Typical use:** Before a systematic review; before designing a new program
- **Academic physician use:** "What do we know about bedside CDI query programs?" before designing a new workflow
- **Time estimate:** 1–3 weeks

### Systematic Review
- **Goal:** Aggregate, critically appraise, and synthesize ALL eligible evidence to answer a narrow, well-defined question
- **Coverage:** Exhaustive — multiple databases, grey literature, no language restriction, a priori protocol (PROSPERO registration recommended)
- **Appraisal:** Risk of bias + quality of evidence required; validated instruments (AMSTAR-2, RoB 2.0, COREQ, STROBE; GRADE is a reference methodology, not implemented by the pipeline)
- **Synthesis:** Meta-analysis if sufficient homogeneity; qualitative synthesis (vote counting, content analysis, tabulation) otherwise
- **Typical use:** Informing clinical guidelines; evidence-based policy; formal publications
- **Academic physician use:** "What is the strength of evidence for nurse-driven foley removal protocols on CAUTI rates?" for quality committee presentation
- **Time estimate:** 3–8 weeks (with this agent: 2–4 weeks across ~6 sessions)

### Umbrella Review (Overview of SRs)
- **Goal:** Compare and contrast findings across multiple systematic reviews on the same broad topic
- **Coverage:** Exhaustive search for systematic reviews only; primary studies are NOT the unit of analysis
- **Appraisal:** AMSTAR-2 applied to each included SR (GRADE is reference only; not implemented by the pipeline)
- **Synthesis:** Tabular comparison of SR findings; narrative comparison; sometimes indirect statistical comparisons
- **Typical use:** Clinical guidelines development; policy decisions spanning multiple interventions
- **Academic physician use:** "What do all the SRs say about sepsis bundle components?" for hospital policy
- **Time estimate:** 2–4 weeks

### Realist Review
- **Goal:** Explain HOW and WHY complex interventions work, for WHOM, in WHAT circumstances — not just IF they work
- **Coverage:** Iterative, purposive — search continues until theoretical saturation; not exhaustive by design
- **Appraisal:** Different instruments for qualitative vs. quantitative components
- **Synthesis:** Qualitative (aggregative or interpretive); produces program theory or "realist logic"
- **Typical use:** Complex social interventions; implementation science; context-sensitive questions
- **Academic physician use:** "Why does our readmission reduction program work at Hospital A but not Hospital B?"
- **Time estimate:** 4–12 weeks (most iterative; requires theoretical expertise)

### Critical Review
- **Goal:** Evaluate strengths, weaknesses, and contradictions in prior work; identify methodological problems; propose new frameworks
- **Coverage:** Representative — not necessarily exhaustive; selected for their importance to the critique
- **Appraisal:** None required (though methodological critique IS the content)
- **Synthesis:** Positivist (content analysis) or interpretivist (meta-ethnography)
- **Typical use:** Academic journals; advancing theoretical debates
- **Academic physician use:** Less common; potentially useful for critiquing a payer's evidence basis for a coverage decision

---

## Academic Physician Use-Case Mapping

| Use Case | Best Review Type | Why |
|---|---|---|
| "Is there evidence for X?" | Scoping | Map the landscape first |
| "What is the strength of evidence for X?" | Systematic | Need rigorous appraisal |
| "Quick evidence for C-suite presentation" | Narrative | Speed + clarity over exhaustiveness |
| "What do all the SRs say about X?" | Umbrella | Synthesize existing SRs efficiently |
| "Why does X work at our hospital but not others?" | Realist | Context-sensitivity question |
| "Preparation for a payer appeals/peer-to-peer" | Narrative or Scoping | Time-constrained; need key evidence |
| "Designing a new quality initiative" | Scoping → Systematic | Map first, then answer specific questions |
| "Responding to a Joint Commission standard" | Systematic | Rigorous evidence basis needed |
| "InterQual vs MCG criteria comparison" | Scoping or Systematic | Depends on depth needed |
| "Evidence for a physician query program" | Scoping or Systematic | Depends on whether formal publication planned |
| "Mortality Index improvement evidence" | Systematic | High-stakes, needs rigorous appraisal |

---

## When to Downscope From Systematic to Scoping/Narrative

Consider a scoping review or narrative review instead of systematic when:

- **Time constraint:** Need answer in days, not weeks
- **Question is exploratory:** No specific intervention/outcome comparison in mind yet
- **Literature is sparse:** Insufficient studies for meaningful systematic appraisal
- **Audience doesn't need rigorous methods reporting:** C-suite, staff education vs. peer-reviewed publication
- **PICO is too broad:** "What do we know about CDI programs?" is a scoping question, not a systematic one

---

## Coverage Strategy Decision

From Cooper (1988) via Paré & Kitsiou (2015):

| Strategy | Description | When to Use |
|---|---|---|
| **Exhaustive** | Every relevant published and unpublished study | Systematic review; formal publication |
| **Representative** | Top-tier journals; representative sample of a field | Narrative review; scoping review |
| **Pivotal-works** | Seminal/landmark papers only | Narrative review; quick background |

---

## Time and Effort Estimates (with agent assistance)

| Review Type | Build Session Count | Elapsed Calendar Time | Token Budget |
|---|---|---|---|
| Narrative | 1–2 sessions | 1–3 days | Low |
| Scoping | 2–3 sessions | 1–2 weeks | Moderate |
| Systematic | 5–7 sessions | 2–5 weeks | High |
| Umbrella | 3–4 sessions | 1–3 weeks | Moderate |
| Realist | 4–8 sessions | 3–8 weeks | High (iterative) |
| Critical | 1–2 sessions | 1–2 weeks | Low–Moderate |

Calendar time includes the multi-day gap for hospital library PDF retrieval (between Sessions 3 and 4 for systematic reviews).

---

## How the Agent Routes by Review Type

The `review_type_classifier` skill sets the review type at Stage 1. It is stored in `state.json` and shapes the Claude-written protocol and queries, but the pipeline stages are identical for every type: `quality_appraiser`, `multi_source_retriever`, and `synthesizer` never read `review_type`. Default sources are PubMed, OpenAlex, and Semantic Scholar (Europe PMC is opt-in). The table below is advisory guidance for the human (`DEPTH_BY_TYPE` in `review_type_classifier.py` is advisory text), not enforced routing:

| Review Type | Search Coverage (advisory) | Appraisal (advisory) | Synthesis Method (advisory) |
|---|---|---|---|
| Narrative | Representative (agent suggests 2–3 databases) | No | `synthesizer` produces thematic narrative only |
| Scoping | Comprehensive (default sources, plus Europe PMC if wanted) | No | Analytic framework + frequency tables |
| Systematic | Exhaustive (default sources, plus Europe PMC if wanted; grey lit check by hand) | Yes (auto-select instrument) | `synthesizer` produces narrative + evidence map + quantitative scaffold |
| Umbrella | Exhaustive for SRs only (restrict by hand in the queries) | Yes (AMSTAR-2) | Tabular SR comparison |
| Realist | Iterative (agent iterates until the reviewer signals saturation) | Mixed | Qualitative synthesis |
| Critical | Representative | No | Interpretive narrative |

---

## Synthesis Evidence Base: Full-Text-Only by Default

**Default (PRISMA-compliant):** synthesis is built only from articles that cleared full-text eligibility (Stage 10) and were quality-appraised (Stage 13). Articles that cleared title/abstract screening but never reached full-text — typically because the PDF couldn't be retrieved via the OA waterfall (left in `library_request.csv`) — are **excluded** from `by_outcome`, `evidence_map`, `quantitative_scaffold`, and the executive summary's main narrative.

**Opt-in abstract supplement (Stage 16 Step 1):** if the user explicitly opts in, the agent renders abstract-only-eligible articles in a clearly-labeled appendix (`Appendix — Supplementary Abstract-Only Evidence (Not Full-Text Appraised)`) appended to the executive summary. This appendix is **never** folded into the appraised evidence base — it sits outside the main narrative, after a horizontal rule, with explicit caveats that the articles were not full-text appraised. The opt-in defaults to **no** to preserve the protocol; choose yes only when downstream readers (C-suite, quality committee) would benefit from awareness of unretrievable evidence and you accept the labeling.
