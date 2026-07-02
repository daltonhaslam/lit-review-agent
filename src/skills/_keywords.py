"""Shared keyword utilities: stopwords, synonym families, term extraction.

Used by citation_chaser (keyword prefilter) and query_builder (concept extraction).
"""
from __future__ import annotations

import re

_STOP_WORDS = frozenset({
    "the", "and", "or", "not", "for", "with", "from", "that", "this", "which",
    "are", "was", "were", "been", "have", "has", "had", "will", "would", "could",
    "should", "may", "might", "can", "shall", "its", "their", "these", "those",
    "such", "only", "both", "also", "than", "more", "most", "each", "any", "all",
    "who", "what", "where", "when", "how", "why", "into", "onto", "upon", "about",
    "after", "before", "during", "within", "between", "among", "without", "through",
    "required", "defined", "formal", "other", "versus", "non", "hub", "spoke",
    "patient", "patients", "study", "studies", "review", "reviews", "reported",
    "program", "programs", "site", "sites", "setting", "settings",
})

# Synonym families by category: (category, trigger_set, expansion_set).
# Triggers are matched against raw PICO text terms; when triggered, expansion_set
# is added to the p_terms or i_terms pool used for candidate filtering.
# "population" families → p_terms; "intervention" families → i_terms.
_SYNONYM_FAMILIES: list[tuple[str, frozenset, frozenset]] = [
    (
        "population",
        frozenset({"pediatric", "paediatric", "neonatal", "infant", "child", "children",
                   "newborn", "perinatal", "neonate", "pediatrics", "paediatrics"}),
        frozenset({"pediatric", "paediatric", "neonatal", "infant", "child", "children",
                   "newborn", "perinatal", "neonate", "pediatrics", "paediatrics"}),
    ),
    (
        "population",
        frozenset({"adult", "geriatric", "elderly", "older adults", "gerontology"}),
        frozenset({"adult", "adults", "geriatric", "elderly", "older adults", "gerontology", "aged"}),
    ),
    (
        "population",
        frozenset({"oncology", "cancer", "tumor", "tumour", "malignancy", "carcinoma",
                   "lymphoma", "leukemia", "leukaemia", "neoplasm"}),
        frozenset({"oncology", "cancer", "tumor", "tumour", "malignancy", "carcinoma",
                   "lymphoma", "leukemia", "leukaemia", "neoplasm", "malignant"}),
    ),
    (
        "intervention",
        frozenset({"telemedicine", "telehealth", "teleconsult", "econsult", "telemed",
                   "tele-medicine", "tele-health", "tele-consult"}),
        frozenset({"telemedicine", "telehealth", "teleconsult", "econsult", "telemed",
                   "tele-medicine", "tele-health", "tele-consult", "virtual consult",
                   "remote consult", "e-consult"}),
    ),
    (
        "intervention",
        frozenset({"readmission", "readmissions", "rehospitalization", "rehospitalisation",
                   "re-admission", "re-hospitalization"}),
        frozenset({"readmission", "readmissions", "rehospitalization", "rehospitalisation",
                   "re-admission", "re-hospitalization", "readmitted"}),
    ),
    (
        "intervention",
        frozenset({"observation", "observation status", "observation unit", "obs unit"}),
        frozenset({"observation", "observation status", "observation unit", "obs unit",
                   "outpatient observation", "inpatient observation"}),
    ),
]


def _extract_terms(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", text.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) >= 4}
