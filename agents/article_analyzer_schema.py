from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


EventType = Literal[
    "GOVERNMENT_CONTRACT",
    "COMMERCIAL_CONTRACT",
    "PRODUCT_LAUNCH",
    "SATELLITE_LAUNCH",
    "LAUNCH_SUCCESS",
    "LAUNCH_FAILURE",
    "FUNDING",
    "EARNINGS",
    "ACQUISITION",
    "MERGER",
    "PARTNERSHIP",
    "REGULATORY_CHANGE",
    "GOVERNMENT_POLICY",
    "TECHNOLOGY_MILESTONE",
    "CAPACITY_EXPANSION",
    "LEADERSHIP_CHANGE",
    "LEGAL_OR_COMPLIANCE",
    "OTHER",
    "NO_EVENT",
]

ImpactDirection = Literal[
    "POSITIVE",
    "NEGATIVE",
    "NEUTRAL",
    "MIXED",
    "UNCERTAIN",
]

TimeHorizon = Literal[
    "IMMEDIATE",
    "SHORT_TERM",
    "MEDIUM_TERM",
    "LONG_TERM",
    "UNCERTAIN",
]


class ArticleAnalysisOutput(BaseModel):
    """Strict structured output contract for one article analysis."""

    has_event: bool
    event_type: EventType
    impact_direction: Optional[ImpactDirection]
    impact_strength: Optional[int] = Field(ge=1, le=5)
    importance_score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    companies: list[str] = Field(max_length=20)
    topics: list[str] = Field(max_length=20)
    time_horizon: TimeHorizon
    event_date: Optional[date]
    summary: str = Field(min_length=1, max_length=1000)
    industry_implication: Optional[str] = Field(max_length=2000)
    facts: list[str] = Field(min_length=1, max_length=20)
    risk_factors: list[str] = Field(max_length=20)

    @field_validator(
        "summary",
        "industry_implication",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None
        normalized = " ".join(str(value).strip().split())
        return normalized or None

    @field_validator(
        "companies",
        "topics",
        "facts",
        "risk_factors",
        mode="before",
    )
    @classmethod
    def normalize_text_lists(cls, values):
        if values is None:
            return []

        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = " ".join(str(value).strip().split())
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            normalized.append(text)
        return normalized

    @model_validator(mode="after")
    def validate_event_consistency(self):
        if self.has_event:
            if self.event_type == "NO_EVENT":
                raise ValueError("A real event cannot use event_type NO_EVENT.")
            if self.impact_direction is None:
                raise ValueError("A real event requires impact_direction.")
            if self.impact_strength is None:
                raise ValueError("A real event requires impact_strength.")
        else:
            if self.event_type != "NO_EVENT":
                raise ValueError("has_event=false requires event_type NO_EVENT.")
            if self.impact_direction is not None:
                raise ValueError("NO_EVENT requires impact_direction=null.")
            if self.impact_strength is not None:
                raise ValueError("NO_EVENT requires impact_strength=null.")
            if self.event_date is not None:
                raise ValueError("NO_EVENT requires event_date=null.")

        return self
