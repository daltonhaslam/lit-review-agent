from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from .pico import PICO
from .query import QuerySet


class ReviewType(str, Enum):
    narrative = "narrative"
    scoping = "scoping"
    systematic = "systematic"
    umbrella = "umbrella"
    realist = "realist"
    critical = "critical"


class ScreeningMode(str, Enum):
    thorough = "thorough"
    fast = "fast"


class ReviewStatus(str, Enum):
    # Stage names map to the 17-stage pipeline
    new = "new"
    review_type_classified = "review_type_classified"
    pico_formulated = "pico_formulated"
    protocol_generated = "protocol_generated"
    queries_built = "queries_built"
    retrieved = "retrieved"
    aggregated = "aggregated"
    deduplicated = "deduplicated"
    abstract_screening = "abstract_screening"
    retrieval_list_generated = "retrieval_list_generated"
    pdfs_downloaded = "pdfs_downloaded"
    fulltext_assessed = "fulltext_assessed"
    data_extracted = "data_extracted"
    enriched = "enriched"
    quality_appraised = "quality_appraised"
    evidence_classified = "evidence_classified"
    citation_chased = "citation_chased"
    # Legacy: no longer written (iteration_manager.merge now advances to fulltext_assessed).
    # Retained for backward-compat so on-disk state.json from older runs still loads.
    abstract_screened_iteration = "abstract_screened_iteration"
    gap_analyzed = "gap_analyzed"
    synthesized = "synthesized"
    complete = "complete"

    def at_or_before(self, other: "ReviewStatus") -> bool:
        """True if this status is at or before `other` in pipeline (enum-declaration) order.

        Used to guard early stages (pico_refiner / review_type_classifier) from regressing the
        status pointer on an out-of-order re-run. NB: the enum order is the pipeline order only
        near the front — citation_chased / abstract_screened_iteration are declared late but run
        at Stage 8.5, so this is reliable for early-stage guards, not for late-stage ones.
        """
        order = list(ReviewStatus)
        return order.index(self) <= order.index(other)


class PRISMACounts(BaseModel):
    identified: int = 0
    screened: int = 0
    eligibility: int = 0          # reports sought for full-text retrieval (include + borderline)
    fulltext_assessed: int = 0    # of those sought, how many had a PDF and were assessed
    not_retrieved: int = 0        # sought but no PDF available (eligibility - fulltext_assessed)
    included: int = 0
    citation_chasing_added: int = 0
    citation_chasing_borderlines: int = 0
    # PRISMA 2020 citation-chasing stream counts
    citation_chasing_identified: int = 0
    citation_chasing_screened: int = 0
    # Exclusion reasons (for PRISMA flow)
    excluded_screening: Dict[str, int] = Field(default_factory=dict)
    excluded_fulltext: Dict[str, int] = Field(default_factory=dict)


class DateRange(BaseModel):
    start: Optional[int] = None  # publication year (inclusive)
    end: Optional[int] = None    # publication year (inclusive)


class Review(BaseModel):
    review_id: str
    review_type: Optional[ReviewType] = None
    screening_mode: ScreeningMode = ScreeningMode.thorough
    status: ReviewStatus = ReviewStatus.new
    raw_question: str = ""

    pico: Optional[PICO] = None
    queries: QuerySet = Field(default_factory=QuerySet)
    sources_searched: list[str] = Field(default_factory=list)
    date_range: Optional[DateRange] = None

    prisma_counts: PRISMACounts = Field(default_factory=PRISMACounts)

    # File references (relative to review dir)
    articles_raw_file: str = "articles_raw.json"
    articles_deduped_file: str = "articles_deduped.json"
    articles_screened_file: str = "articles_screened.json"
    articles_included_file: str = "articles_included.json"

    protocol_locked_at: Optional[datetime] = None
    iterations: int = 0
    max_iterations: int = 3
    seen_article_ids: list[str] = Field(default_factory=list)

    # Packaging metadata (set by /lit-review-package; backwards-compatible)
    packaged: bool = False
    packaged_at: Optional[datetime] = None
    packaged_artifacts: list[str] = Field(default_factory=list)
    packaged_pandoc_version: Optional[str] = None
    packaged_audit_summary: Dict[str, int] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
