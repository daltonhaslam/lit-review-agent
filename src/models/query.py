from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field


class Query(BaseModel):
    step_number: str  # e.g. "#1", "#2", "#3"
    query_string: str
    database: str
    hits: Optional[int] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: Optional[str] = None
    web_env: Optional[str] = None   # PubMed history server WebEnv
    query_key: Optional[str] = None  # PubMed history server query_key


class QuerySet(BaseModel):
    pubmed: list[Query] = Field(default_factory=list)
    openalex: list[Query] = Field(default_factory=list)
    semantic_scholar: list[Query] = Field(default_factory=list)
    europepmc: list[Query] = Field(default_factory=list)
