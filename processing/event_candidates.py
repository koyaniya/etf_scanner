from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any


DEFAULT_EVENT_CANDIDATE_WINDOW_DAYS = 30
DEFAULT_EVENT_CANDIDATE_LIMIT = 10


@dataclass(frozen=True)
class EventCandidate:
    event_id: int
    canonical_title: str
    event_type: str
    event_date: str | None
    company_ids: list[int]
    topic_ids: list[int]
    company_overlap: float
    topic_overlap: float
    days_apart: int | None
    similarity_score: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_candidate_window_days() -> int:
    value = os.getenv(
        "EVENT_CANDIDATE_WINDOW_DAYS",
        str(DEFAULT_EVENT_CANDIDATE_WINDOW_DAYS),
    )
    try:
        days = int(value)
    except ValueError as exc:
        raise ValueError("EVENT_CANDIDATE_WINDOW_DAYS must be an integer.") from exc
    if days < 1:
        raise ValueError("EVENT_CANDIDATE_WINDOW_DAYS must be at least 1.")
    return days


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _overlap_coefficient(left: set[int], right: set[int]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _jaccard(left: set[int], right: set[int]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def rank_event_candidates(
    analysis: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    window_days: int,
    limit: int = DEFAULT_EVENT_CANDIDATE_LIMIT,
) -> list[EventCandidate]:
    """Return a broad deterministic shortlist; it does not make merge decisions."""

    if window_days < 1:
        raise ValueError("window_days must be at least 1.")
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    if not analysis.get("has_event") or analysis.get("event_type") == "NO_EVENT":
        return []

    analysis_companies = set(analysis.get("company_ids") or [])
    analysis_topics = set(analysis.get("topic_ids") or [])
    analysis_date = _as_date(
        analysis.get("event_date") or analysis.get("published_at")
    )
    candidates: list[EventCandidate] = []

    for event in events:
        if event.get("status") != "ACTIVE":
            continue
        if event.get("event_type") != analysis.get("event_type"):
            continue

        event_date = _as_date(
            event.get("event_date")
            or event.get("last_published_at")
            or event.get("first_published_at")
        )
        days_apart = (
            abs((analysis_date - event_date).days)
            if analysis_date is not None and event_date is not None
            else None
        )
        if days_apart is not None and days_apart > window_days:
            continue

        company_ids = set(event.get("company_ids") or [])
        topic_ids = set(event.get("topic_ids") or [])
        company_overlap = _overlap_coefficient(
            analysis_companies, company_ids
        )
        topic_overlap = _jaccard(analysis_topics, topic_ids)
        temporal_score = (
            max(0.0, 1 - (days_apart / window_days))
            if days_apart is not None
            else 0.0
        )

        # Event type is a required filter. Company identity carries the most
        # weight; topics and time narrow otherwise plausible matches.
        score = (
            0.25
            + (0.45 * company_overlap)
            + (0.20 * topic_overlap)
            + (0.10 * temporal_score)
        )
        candidates.append(EventCandidate(
            event_id=event["event_id"],
            canonical_title=event.get("canonical_title") or "",
            event_type=event["event_type"],
            event_date=(str(event.get("event_date"))
                        if event.get("event_date") else None),
            company_ids=sorted(company_ids),
            topic_ids=sorted(topic_ids),
            company_overlap=round(company_overlap, 4),
            topic_overlap=round(topic_overlap, 4),
            days_apart=days_apart,
            similarity_score=round(score, 4),
        ))

    candidates.sort(
        key=lambda row: (
            -row.similarity_score,
            row.days_apart if row.days_apart is not None else window_days + 1,
            row.event_id,
        )
    )
    return candidates[:limit]


def load_analysis_context(
    supabase: Any,
    analysis_id: int,
) -> dict[str, Any]:
    response = (supabase.table("article_analyses")
        .select(
            "analysis_id,article_id,status,has_event,event_type,event_date,"
            "impact_direction,impact_strength,importance_score,confidence,"
            "time_horizon,summary,industry_implication,facts,risk_factors,"
            "analysis_version"
        )
        .eq("analysis_id", analysis_id).limit(1).execute())
    if not response.data:
        raise RuntimeError(f"Analysis {analysis_id} was not found.")
    analysis = response.data[0]
    if analysis.get("status") != "SUCCESS":
        raise RuntimeError(f"Analysis {analysis_id} is not successful.")
    if not analysis.get("has_event"):
        raise RuntimeError(f"Analysis {analysis_id} does not contain an event.")

    article = (supabase.table("articles").select("title,published_at")
        .eq("article_id", analysis["article_id"]).limit(1).execute())
    article_row = article.data[0] if article.data else {}
    analysis["article_title"] = article_row.get("title")
    analysis["published_at"] = article_row.get("published_at")
    company_rows = (supabase.table("article_analysis_companies")
        .select("company_id").eq("analysis_id", analysis_id).execute())
    topic_rows = (supabase.table("article_analysis_topics")
        .select("topic_id").eq("analysis_id", analysis_id).execute())
    analysis["company_ids"] = [
        row["company_id"] for row in (company_rows.data or [])
    ]
    analysis["topic_ids"] = [
        row["topic_id"] for row in (topic_rows.data or [])
    ]
    return analysis


def load_active_events_for_type(
    supabase: Any,
    event_type: str,
) -> list[dict[str, Any]]:
    response = (supabase.table("events")
        .select(
            "event_id,status,canonical_title,event_type,event_date,"
            "first_published_at,last_published_at,canonical_summary,facts"
        )
        .eq("status", "ACTIVE").eq("event_type", event_type).execute())
    events = response.data or []
    if not events:
        return []

    event_ids = [row["event_id"] for row in events]
    company_rows = (supabase.table("event_companies")
        .select("event_id,company_id").in_("event_id", event_ids).execute())
    topic_rows = (supabase.table("event_topics")
        .select("event_id,topic_id").in_("event_id", event_ids).execute())
    companies_by_event: dict[int, list[int]] = {}
    topics_by_event: dict[int, list[int]] = {}
    for row in company_rows.data or []:
        companies_by_event.setdefault(row["event_id"], []).append(row["company_id"])
    for row in topic_rows.data or []:
        topics_by_event.setdefault(row["event_id"], []).append(row["topic_id"])
    for event in events:
        event["company_ids"] = companies_by_event.get(event["event_id"], [])
        event["topic_ids"] = topics_by_event.get(event["event_id"], [])
    return events


def find_event_candidates(
    supabase: Any,
    analysis_id: int,
    *,
    window_days: int | None = None,
    limit: int = DEFAULT_EVENT_CANDIDATE_LIMIT,
) -> tuple[dict[str, Any], list[EventCandidate]]:
    analysis = load_analysis_context(supabase, analysis_id)
    events = load_active_events_for_type(supabase, analysis["event_type"])
    candidates = rank_event_candidates(
        analysis,
        events,
        window_days=(window_days if window_days is not None
                     else get_candidate_window_days()),
        limit=limit,
    )
    return analysis, candidates
