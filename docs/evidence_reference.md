# Evidence Levels & Quality Appraisal — Quick Reference
_Last updated: April 2026_

Source: Considine et al. (2017), Paré & Kitsiou (2015), NHMRC, Cochrane, GRADE Working Group

---

## NHMRC Evidence Levels

Applied by the `evidence_classifier` skill to each included article.

| Level | Study Design | Description |
|---|---|---|
| **I** | Systematic review | Systematic review of all relevant randomized controlled trials |
| **II** | RCT | At least one properly designed randomized controlled trial |
| **III-1** | Pseudorandomised trial | Well-designed controlled trial without true randomization |
| **III-2** | Comparative, concurrent controls | Cohort study, case-control study, or interrupted time series WITH a parallel control group |
| **III-3** | Comparative, historical controls | Studies with historical control group; two or more single-arm studies; interrupted time series WITHOUT a parallel control |
| **IV** | Case series | Post-test only, or pre-test/post-test without a control group |

### How the classifier maps study designs (implementation)

The definitions above are the NHMRC 2009 reference. `evidence_classifier` does not apply them directly; it maps each canonical `study_design` ID (`src/skills/_study_design.py`, `CANONICAL_TO_NHMRC`) to a level:

| Level | Canonical study designs mapped by the code |
|---|---|
| I | systematic review, meta-analysis, umbrella review |
| II | RCT, cluster RCT |
| III-1 | quasi-RCT, non-randomized trial, controlled before-after, interrupted time series |
| III-2 | prospective cohort, comparative study |
| III-3 | retrospective cohort, case-control, historical control |
| IV | case series, case report, cross-sectional, qualitative, mixed methods, survey, economic evaluation, narrative/scoping review, editorial, commentary, letter, expert opinion, program description, QI report |

**Known deviation, pending maintainer review:** compared with the reference table above, the code places retrospective cohort and case-control studies one level lower (III-3, not III-2), and places interrupted time series and controlled before-after one level higher (III-1, not III-2/III-3 depending on a parallel control). Designs the reference table does not cover (cross-sectional, qualitative, survey, economic evaluation) default to IV.

**"Highest available" principle:** The focus is on determining the highest *available* level of evidence. Many clinical questions cannot achieve Level I/II. Level III or IV evidence may be the best available and still clinically actionable. Document this clearly in the executive summary.

---

## GRADE Evidence Quality

_Reference only — not implemented by the pipeline. No code in `src/` assigns GRADE ratings._

Applied to specific clinical outcome recommendations (different from NHMRC which applies to study design).

| GRADE Level | Meaning | Typical Basis |
|---|---|---|
| **High** | Very confident the true effect is close to the estimate | Well-conducted RCTs with consistent results |
| **Moderate** | Moderately confident; true effect probably close to estimate but may differ | RCTs with important limitations, or well-conducted observational |
| **Low** | Limited confidence; true effect may differ substantially | RCTs with very serious limitations, or observational |
| **Very Low** | Very little confidence; estimate highly uncertain | Case series, expert opinion, very indirect evidence |

GRADE can be **downgraded** by: risk of bias, inconsistency, indirectness, imprecision, publication bias.
GRADE can be **upgraded** by: large effect size, dose-response gradient, all plausible confounders would reduce the effect.

---

## Quality Appraisal Instruments

The `quality_appraiser` skill auto-selects the appropriate instrument based on study design detected during data extraction.

### AMSTAR-2 — Systematic Reviews
**16 items; 7 are critical domains.**

Critical domains (a "no" here = critically low quality):
1. PICO components included in the research question?
2. Protocol registered before review commenced?
3. Comprehensive literature search (2+ databases, grey lit, date restrictions justified)?
4. Study selection performed in duplicate?
5. Data extraction performed in duplicate?
6. Satisfactory technique for assessing risk of bias in individual studies?
7. Did the review authors account for risk of bias when interpreting results?

Overall rating: High / Moderate / Low / Critically Low

---

### Cochrane Risk of Bias 2.0 (RoB 2.0) — RCTs
**5 domains; each rated Low / Some Concerns / High risk of bias.**

| Domain | What It Assesses |
|---|---|
| D1: Randomization process | Was the randomization sequence well-generated? Concealment adequate? |
| D2: Deviations from interventions | Were participants blinded? Were deviations from protocol handled appropriately? |
| D3: Missing outcome data | Were missing data handled appropriately? Could the result be biased by missing data? |
| D4: Outcome measurement | Was the outcome measurement method appropriate? Were assessors blinded? |
| D5: Selection of the reported result | Was the result reported on the basis of what was found (fishing)? |

Overall bias judgment: Low / Some Concerns / High

---

### GRADE for Outcomes — Evidence Summaries
_Reference only — not implemented by the pipeline._

Applied to the body of evidence for each outcome across all included studies. Produces a Summary of Findings table.

**Process:**
1. Start at "High" if body of evidence = RCTs; start at "Low" if observational
2. Downgrade for: risk of bias, inconsistency, indirectness, imprecision, publication bias
3. Upgrade for: large effect, dose-response, residual confounding would reduce effect
4. Final rating: High / Moderate / Low / Very Low per outcome

---

### COREQ (Consolidated Criteria for Reporting Qualitative Research)
**32 items across 3 domains.** Used for qualitative studies.

| Domain | Items |
|---|---|
| Research team and reflexivity | Characteristics of interviewer/facilitator; relationship with participants |
| Study design | Theoretical framework; sampling; setting; data collection |
| Analysis and findings | Data analysis; reporting; quotations presented |

---

### STROBE (Strengthening the Reporting of Observational Studies in Epidemiology)
**22 items.** Used for cohort studies, case-control studies, and cross-sectional studies.

Key domains: title/abstract, introduction, methods (design, setting, participants, variables, bias, sample size, statistics), results (participants, descriptive data, outcome data, main results, other analyses), discussion (key results, limitations, generalizability), funding.

---

## Appraisal Instrument Sources

The `quality_appraiser` skill paraphrases published instruments for prompting; the paraphrases are not verbatim reproductions. Consult the original instruments for authoritative definitions.

| Instrument | Citation |
|---|---|
| AMSTAR-2 | Shea et al., *BMJ* 2017;358:j4008. |
| RoB 2 (Cochrane) | Sterne et al., *BMJ* 2019;366:l4898. |
| STROBE | von Elm et al., *PLoS Med* 2007;4(10):e296 (STROBE Initiative). |
| COREQ | Tong et al., *Int J Qual Health Care* 2007;19(6):349–357. |
| SANRA | Baethge et al., *Res Integr Peer Rev* 2019;4:5 (CC BY 4.0). |

---

## Instrument Selection Guide

```
study_design → appraisal_instrument

"systematic review"          → AMSTAR-2
"meta-analysis"              → AMSTAR-2
"randomized controlled trial"→ Cochrane RoB 2.0
"rct"                        → Cochrane RoB 2.0
"scoping review"             → AMSTAR-2
"cohort study"               → STROBE
"case-control"               → STROBE
"cross-sectional"            → STROBE
"quasi-RCT / non-randomized" → STROBE (checked before the RCT rule)
"qualitative"                → COREQ
"narrative / policy review"  → SANRA
"editorial / commentary /
 letter / expert opinion"    → Expert Opinion rubric
"mixed methods"              → STROBE (default fall-through; no mixed-methods branch)
anything unrecognized        → STROBE

(GRADE is reference only and is not part of this mapping.)
```

---

## PRISMA 2020 Flow Diagram

Track these counters in `state.json` → `prisma_counts`. Generates `prisma_flow.md`.

_Illustrative layout only._ The generated `prisma_flow.md` (`_write_prisma_flow` in `output_formatter.py`) uses a two-stream PRISMA 2020 layout (databases; citation searching) and does not report per-source counts.

```
┌──────────────────────────────────────────┐
│ IDENTIFICATION                           │
│ Records from databases: n = ___          │
│   PubMed: ___                            │
│   OpenAlex: ___                          │
│   Semantic Scholar: ___                  │
│ Records removed (duplicates): n = ___    │
│ Records after deduplication: n = ___     │
└──────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────┐
│ SCREENING                                │
│ Records screened: n = ___               │
│ Records excluded (title/abstract): n=___ │
│   Exclusion reasons:                     │
│     Wrong population: ___               │
│     Wrong intervention: ___             │
│     Wrong outcome: ___                  │
│     Wrong study design: ___             │
│     Duplicate: ___                       │
│     Other: ___                           │
└──────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────┐
│ ELIGIBILITY                              │
│ Reports sought for retrieval: n = ___   │
│ Reports not retrieved: n = ___           │
│ Reports assessed for eligibility: n =___ │
│ Reports excluded (full text): n = ___   │
│   Exclusion reasons: [detail per above]  │
└──────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────┐
│ INCLUDED                                 │
│ Studies included in review: n = ___     │
│ Additional via citation chasing: n = ___ │
│ TOTAL INCLUDED: n = ___                  │
└──────────────────────────────────────────┘
```

---

## Evidence Map Template

Populate after synthesis. Rows = outcomes; columns = study design. Shows where evidence is strong vs. thin.

| Outcome | SR (Level I) | RCT (Level II) | Cohort (III-2) | Case Series (IV) | GRADE Quality (illustrative; manual, not pipeline-generated) |
|---|---|---|---|---|---|
| 30-day readmission | 3 SRs | 5 RCTs | 2 cohort | — | Moderate |
| ED visits | 1 SR | 2 RCTs | — | — | Low |
| Mortality | — | 3 RCTs | 1 cohort | — | Low |
| Patient satisfaction | — | 2 RCTs | — | 1 | Very Low |

---

## Surrogate Outcome Warning

From Considine et al. (2017): *"Stay true to the outcomes of interest listed in the PICO question. The reason so many studies use surrogate outcomes is because it is easier, faster and often cheaper than using complex, hard clinical outcomes."*

**Common surrogate outcome pitfalls in academic physician work:**

| PICO Outcome | Common Surrogate | Problem |
|---|---|---|
| 30-day readmission | Hospital LOS | LOS doesn't predict readmission |
| Mortality | BNP / LVEF | Biomarkers ≠ survival |
| Patient experience | Press-Ganey score | Survey ≠ HCAHPS CAHPS score |
| Cost reduction | Billing charge | Charges ≠ cost |

The `evidence_classifier` skill flags surrogate outcomes (by comparing each article's primary outcomes to the PICO outcomes). The `synthesizer` and `gap_analyzer` consume the flag and note it explicitly.

---

## Notes on Applying Evidence Levels in Academic Physician Work

- **Don't downgrade based on study type alone.** A well-conducted cohort study may be more generalizable to your health system's population than an RCT conducted in a different setting.
- **Consistency across studies matters.** Multiple Level III-2 studies consistently showing the same effect are often stronger evidence than one Level II study with methodological concerns.
- **Recency matters for clinical guidelines.** A 2022 systematic review supersedes a 2015 RCT for current clinical practice.
- **Heterogeneity is real.** The right intervention for a community hospital may differ from academic medical center evidence. Note this in the executive summary.
- **For C-suite audiences:** simplify to "strong/moderate/limited evidence" rather than NHMRC levels. Include level detail in the evidence table for clinical staff.
