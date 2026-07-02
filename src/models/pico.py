from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class FundamentalFour(BaseModel):
    what_we_know: str = ""
    what_we_dont_know: str = ""
    what_we_should_know: str = ""
    why_we_should_know: str = ""


class PICO(BaseModel):
    P: str = Field(description="Population / Patient")
    I: str = Field(description="Intervention / Exposure")
    C: str = Field(description="Comparator / Control")
    O: list[str] = Field(description="Outcome(s)", default_factory=list)
    S: Optional[str] = Field(default=None, description="Study design filter")

    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    fundamental_four: Optional[FundamentalFour] = None
    raw_question: str = ""
