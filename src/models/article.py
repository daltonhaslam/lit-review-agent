from __future__ import annotations

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class ScreeningDecision(str, Enum):
    include = "include"
    exclude = "exclude"
    borderline = "borderline"
    pending = "pending"


class EvidenceLevel(str, Enum):
    level_I = "I"
    level_II = "II"
    level_III_1 = "III-1"
    level_III_2 = "III-2"
    level_III_3 = "III-3"
    level_IV = "IV"
    unknown = "unknown"


class ExtractionSource(str, Enum):
    """Records which input Stage 11 used for a given article. Listed in preference order."""
    full_text = "full_text"
    abstract = "abstract"
    tldr = "tldr"
    none = "none"


class Article(BaseModel):
    # Identifiers
    pmid: Optional[str] = None
    doi: Optional[str] = None
    s2_id: Optional[str] = None
    openalex_id: Optional[str] = None

    # Core metadata
    title: str
    abstract: Optional[str] = None
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    journal: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None

    # Source tracking
    source_dbs: list[str] = Field(default_factory=list)

    # MeSH / concepts
    mesh_terms: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)

    # Enrichment (Semantic Scholar / OpenAlex)
    citation_count: Optional[int] = None
    citation_velocity: Optional[float] = None
    influential_citation_count: Optional[int] = None
    tldr: Optional[str] = None

    # Open access
    oa_status: Optional[bool] = None
    oa_pdf_url: Optional[str] = None

    # Appraisal outputs (populated by later stages)
    evidence_level: Optional[EvidenceLevel] = None
    quality_score: Optional[float] = None
    quality_instrument: Optional[str] = None
    quality_notes: Optional[str] = None

    # Screening
    screening_decision: ScreeningDecision = ScreeningDecision.pending
    screening_rationale: Optional[str] = None
    screening_reason_category: Optional[str] = None
    screening_pass1: Optional[str] = None
    screening_pass2: Optional[str] = None
    screening_disagreement: bool = False

    # Full-text eligibility
    fulltext_decision: Optional[ScreeningDecision] = None
    fulltext_rationale: Optional[str] = None

    # PDF text extracted by Stage 10, capped at FULLTEXT_MAX_CHARS. Consumed by Stages 11/13.
    fulltext_excerpt: Optional[str] = None
    extraction_source: Optional[ExtractionSource] = None

    # Data extraction
    study_design: Optional[str] = None
    sample_size: Optional[int] = None
    follow_up_duration: Optional[str] = None
    intervention_details: Optional[str] = None
    comparison_details: Optional[str] = None
    primary_outcomes: list[str] = Field(default_factory=list)
    effect_sizes: Optional[str] = None
    secondary_outcomes: list[str] = Field(default_factory=list)
    author_conclusions: Optional[str] = None
    funding_source: Optional[str] = None
    coi_declared: Optional[bool] = None

    # Flags
    surrogate_outcome_flag: Optional[bool] = None
    citation_chased: bool = False
    iteration_added: bool = False  # included via a Checkpoint-5 iteration search (separate PRISMA stream)
    abstract_unavailable: bool = False
