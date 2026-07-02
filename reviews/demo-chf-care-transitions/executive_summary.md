# Care Transition Interventions to Reduce 30-Day Readmission in Heart Failure (Illustrative Demo)

**Systematic Literature Review | Demo output — 6 articles | July 2026**

---

> **This is a demo review**, not a real completed systematic review. It was derived from a real,
> larger `/lit-review` output by trimming the included-article set down to 6 open-access articles
> so the folder structure and output format could be shown without shipping a full evidence base
> or copyrighted abstract text. Findings below should not be used for clinical or operational
> decision-making — see the full methodology docs for how a real review is produced.

## Background

Heart failure (HF) is a leading cause of 30-day hospital readmission, and reducing it is a
common quality-improvement priority under CMS's Hospital Readmissions Reduction Program (HRRP).
This demo illustrates how `/lit-review` packages a review examining which care transition
interventions — programs initiated at or around hospital discharge — have evidence for reducing
30-day readmission in adult HF patients.

## Clinical Question (PICO)

**P:** Adult patients (≥18 years) hospitalized with a primary HF diagnosis (HFrEF, HFpEF, or
HFmrEF) being discharged from an acute care hospital
**I:** Care transition interventions initiated at or within 7 days of discharge, including: early
outpatient follow-up, nurse- or pharmacist-led discharge education, transitional care models,
remote monitoring or telemonitoring, patient navigators, medication reconciliation programs, and
multidisciplinary transitional care teams
**C:** Usual care — standard discharge without a structured care transition program
**O:** 30-day all-cause readmission (primary); HF-specific readmission; ED visits; 30-day
mortality; 90-day readmission; quality of life; healthcare costs; medication adherence; time to
first post-discharge follow-up

## Methods Summary

For this demo, a small illustrative search funnel (120 records identified, 95 screened after
deduplication, 14 assessed at full text, 6 included — 5 from database searching and 1 via
citation chasing) stands in for a real search. The 6 included articles span 2009–2024 and include
1 AHRQ-commissioned systematic review, 1 overview of systematic reviews, 3 RCTs, and 1 prospective
cohort. Quality appraisal used AMSTAR-2 (systematic reviews), RoB-2 (RCTs), and STROBE (the cohort
study). Evidence was classified using NHMRC levels I–IV.

## Key Findings by Outcome

**30-Day All-Cause Readmission (Primary):** Three of the six articles bear directly on this
outcome. The AHRQ-commissioned Feltner 2014 review (Annals of Internal Medicine) remains a
foundational synthesis finding that certain transitional care intervention types reduce
readmissions. The Cardiolotse RCT (Schüttig 2024, n=2862, BMC Medicine) found that a cardiac care
navigator program lowered the rate and length of all-cause rehospitalization versus standard
care. A registry-based cohort (Lee 2016, Medical Care) found that earlier outpatient physician
follow-up after discharge was associated with a lower risk of 30-day readmission.

**30-Day HF-Specific Readmission:** STRONG-HF (Adamo 2023, n=1077, European Heart Journal) found
that high-intensity post-discharge care with rapid GDMT up-titration reduced 180-day HF
readmission or death regardless of baseline NT-proBNP. The Home-HF trial (Dar 2009, European
Journal of Heart Failure) found home telemonitoring produced similar mortality to usual specialist
care while reducing clinic/ED visits and unplanned HF rehospitalizations.

**Remote Monitoring (context):** An overview of systematic reviews (Bashi 2017, Journal of
Medical Internet Research) found the evidence for remote monitoring in heart failure most
supportive of established monitoring programs, while noting that newer app- and video-based
approaches need further study.

## Evidence Quality

This demo subset spans one AMSTAR-2-rated systematic review (0.90), one AMSTAR-2-rated overview
of systematic reviews (0.75), three RoB-2-rated RCTs (0.75–0.90), and one STROBE-rated
prospective cohort (0.72). Six articles is far too small a base for a real recommendation — see
`gap_analysis.md` for how the tool documents that limitation even in a real review.

## Evidence Gaps

As in a real review, `/lit-review` documents what is *not* covered: within this subset, there are
no head-to-head comparisons between transition intervention types, no HFpEF-specific subgroup
data, and no directly extractable quantitative effect size for the primary outcome. See
`gap_analysis.md` and `gap_analysis.json` for the full structured output.

## Clinical and Operational Implications

*(Omitted for this demo — a real review's executive summary translates findings into a
resource-ranked intervention bundle for local implementation planning; that step depends on the
full evidence base and is not meaningful for a 6-article illustrative subset.)*

## Conclusion

This demo shows the shape of a completed `/lit-review` output: a PICO-scoped question, a
PRISMA-tracked search funnel, quality-appraised and evidence-leveled included articles, a
structured evidence table, a narrative synthesis by outcome, and a documented gap analysis. The
underlying clinical question — which HF care transition interventions reduce 30-day readmission —
is real and clinically important; the 6-article evidence base shown here is illustrative only.
