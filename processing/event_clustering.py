from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from processing.event_candidates import (
    EventCandidate,
    find_event_candidates,
)


CLUSTERING_VERSION = "v1"
STRONG_MATCH_THRESHOLD = 0.85
MINIMUM_MATCH_MARGIN = 0.15
NEW_EVENT_THRESHOLD = 0.45


@dataclass(frozen=True)
class ClusteringDecision:
    decision: str
    selected_event_id: int | None
    best_similarity: float | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_deterministic_cluster(
    candidates: list[EventCandidate],
) -> ClusteringDecision:
    if not candidates:
        return ClusteringDecision(
            decision="NEW_EVENT",
            selected_event_id=None,
            best_similarity=None,
            reason="No active event candidate matched the type and time window.",
        )

    best = candidates[0]
    runner_up_score = (
        candidates[1].similarity_score if len(candidates) > 1 else 0.0
    )
    margin = best.similarity_score - runner_up_score

    if (
        best.similarity_score >= STRONG_MATCH_THRESHOLD
        and best.company_overlap > 0
        and margin >= MINIMUM_MATCH_MARGIN
    ):
        return ClusteringDecision(
            decision="MATCHED",
            selected_event_id=best.event_id,
            best_similarity=best.similarity_score,
            reason=(
                "Strong company-backed deterministic match with sufficient "
                "separation from the next candidate."
            ),
        )

    if best.similarity_score < NEW_EVENT_THRESHOLD:
        return ClusteringDecision(
            decision="NEW_EVENT",
            selected_event_id=None,
            best_similarity=best.similarity_score,
            reason="All existing candidates are below the new-event threshold.",
        )

    return ClusteringDecision(
        decision="AMBIGUOUS",
        selected_event_id=None,
        best_similarity=best.similarity_score,
        reason=(
            "Candidate evidence is plausible but not safe for an automatic merge."
        ),
    )


def load_unclustered_analyses(
    supabase: Any,
    *,
    limit: int,
    analysis_id: int | None = None,
) -> list[int]:
    query = (supabase.table("article_analyses")
        .select("analysis_id")
        .eq("status", "SUCCESS")
        .eq("has_event", True))
    if analysis_id is not None:
        query = query.eq("analysis_id", analysis_id)
    analyses = query.order("analyzed_at").execute().data or []

    run_query = (supabase.table("event_clustering_runs")
        .select("analysis_id")
        .eq("clustering_version", CLUSTERING_VERSION)
        .eq("status", "SUCCESS"))
    if analysis_id is not None:
        run_query = run_query.eq("analysis_id", analysis_id)
    completed = {row["analysis_id"] for row in (run_query.execute().data or [])}
    return [
        row["analysis_id"] for row in analyses
        if row["analysis_id"] not in completed
    ][:limit]


def start_clustering_run(
    supabase: Any,
    analysis_id: int,
) -> int:
    response = (supabase.table("event_clustering_runs").upsert({
        "analysis_id": analysis_id,
        "clustering_version": CLUSTERING_VERSION,
        "status": "RUNNING",
        "decision": None,
        "decision_method": None,
        "selected_event_id": None,
        "candidate_count": 0,
        "best_similarity": None,
        "model": None,
        "prompt_version": None,
        "decision_reason": None,
        "error_message": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
    }, on_conflict="analysis_id,clustering_version").execute())
    if not response.data:
        raise RuntimeError("Could not create event clustering run record.")
    return response.data[0]["clustering_run_id"]


def finish_clustering_run(
    supabase: Any,
    run_id: int,
    decision: ClusteringDecision,
    *,
    candidate_count: int,
    selected_event_id: int | None,
) -> None:
    (supabase.table("event_clustering_runs").update({
        "status": "SUCCESS",
        "decision": decision.decision,
        "decision_method": "DETERMINISTIC",
        "selected_event_id": selected_event_id,
        "candidate_count": candidate_count,
        "best_similarity": decision.best_similarity,
        "decision_reason": decision.reason,
        "error_message": None,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("clustering_run_id", run_id).execute())


def fail_clustering_run(
    supabase: Any,
    run_id: int,
    error: Exception,
) -> None:
    (supabase.table("event_clustering_runs").update({
        "status": "FAILED",
        "error_message": str(error)[:2000] or error.__class__.__name__,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("clustering_run_id", run_id).execute())


def _canonical_title(analysis: dict[str, Any]) -> str:
    title = " ".join(
        str(analysis.get("article_title") or analysis.get("summary") or "").split()
    )
    if not title:
        raise RuntimeError("Cannot create an event without a title or summary.")
    return title[:500]


def create_canonical_event(
    supabase: Any,
    analysis: dict[str, Any],
) -> int:
    published_at = analysis.get("published_at")
    response = supabase.table("events").insert({
        "status": "ACTIVE",
        "canonical_title": _canonical_title(analysis),
        "event_type": analysis["event_type"],
        "impact_direction": analysis["impact_direction"],
        "impact_strength": analysis["impact_strength"],
        "importance_score": analysis["importance_score"],
        "confidence": analysis["confidence"],
        "time_horizon": analysis["time_horizon"],
        "event_date": analysis.get("event_date"),
        "canonical_summary": analysis["summary"],
        "industry_implication": analysis.get("industry_implication"),
        "facts": analysis.get("facts") or [],
        "risk_factors": analysis.get("risk_factors") or [],
        "first_published_at": published_at,
        "last_published_at": published_at,
        "created_from_analysis_id": analysis["analysis_id"],
    }).execute()
    if not response.data:
        raise RuntimeError("Could not create canonical event.")
    return response.data[0]["event_id"]


def link_analysis_to_event(
    supabase: Any,
    analysis: dict[str, Any],
    event_id: int,
    *,
    match_method: str,
    match_score: float | None,
    reason: str,
) -> None:
    supabase.table("event_articles").upsert({
        "event_id": event_id,
        "analysis_id": analysis["analysis_id"],
        "match_method": match_method,
        "match_score": match_score,
        "match_confidence": analysis.get("confidence"),
        "match_reason": reason,
    }, on_conflict="analysis_id").execute()
    company_rows = [
        {"event_id": event_id, "company_id": company_id,
         "company_role": "AFFECTED"}
        for company_id in analysis.get("company_ids") or []
    ]
    topic_rows = [
        {"event_id": event_id, "topic_id": topic_id}
        for topic_id in analysis.get("topic_ids") or []
    ]
    if company_rows:
        supabase.table("event_companies").upsert(company_rows).execute()
    if topic_rows:
        supabase.table("event_topics").upsert(topic_rows).execute()


def update_event_publication_window(
    supabase: Any,
    event_id: int,
    analysis: dict[str, Any],
) -> None:
    published_at = analysis.get("published_at")
    if published_at is None:
        return
    response = (supabase.table("events")
        .select("first_published_at,last_published_at")
        .eq("event_id", event_id).limit(1).execute())
    event = response.data[0] if response.data else {}
    timestamps = [
        value for value in (
            event.get("first_published_at"),
            event.get("last_published_at"),
            published_at,
        ) if value
    ]
    (supabase.table("events").update({
        "first_published_at": min(timestamps),
        "last_published_at": max(timestamps),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("event_id", event_id).execute())


def run_event_clustering_batch(
    supabase: Any,
    *,
    limit: int,
    save: bool = False,
    analysis_id: int | None = None,
    window_days: int | None = None,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    analysis_ids = load_unclustered_analyses(
        supabase, limit=limit, analysis_id=analysis_id
    )
    results: list[dict[str, Any]] = []

    for selected_analysis_id in analysis_ids:
        run_id: int | None = None
        try:
            if save:
                run_id = start_clustering_run(supabase, selected_analysis_id)
            analysis, candidates = find_event_candidates(
                supabase, selected_analysis_id,
                window_days=window_days,
            )
            decision = decide_deterministic_cluster(candidates)
            selected_event_id = decision.selected_event_id
            if save:
                if decision.decision == "NEW_EVENT":
                    selected_event_id = create_canonical_event(supabase, analysis)
                    link_analysis_to_event(
                        supabase, analysis, selected_event_id,
                        match_method="NEW_EVENT", match_score=decision.best_similarity,
                        reason=decision.reason,
                    )
                elif decision.decision == "MATCHED":
                    if selected_event_id is None:
                        raise RuntimeError("Matched decision has no selected event.")
                    link_analysis_to_event(
                        supabase, analysis, selected_event_id,
                        match_method="DETERMINISTIC",
                        match_score=decision.best_similarity,
                        reason=decision.reason,
                    )
                    update_event_publication_window(
                        supabase, selected_event_id, analysis
                    )
                finish_clustering_run(
                    supabase, run_id, decision,
                    candidate_count=len(candidates),
                    selected_event_id=selected_event_id,
                )
            results.append({
                "analysis_id": selected_analysis_id,
                "status": "SUCCESS",
                "decision": decision.decision,
                "selected_event_id": selected_event_id,
                "candidate_count": len(candidates),
                "decision_reason": decision.reason,
                "candidates": [row.as_dict() for row in candidates],
            })
        except Exception as exc:
            if save and run_id is not None:
                fail_clustering_run(supabase, run_id, exc)
            results.append({
                "analysis_id": selected_analysis_id,
                "status": "FAILED",
                "error": str(exc),
            })

    return {
        "mode": "SAVE" if save else "PREVIEW",
        "clustering_version": CLUSTERING_VERSION,
        "selected": len(analysis_ids),
        "succeeded": sum(row["status"] == "SUCCESS" for row in results),
        "failed": sum(row["status"] == "FAILED" for row in results),
        "new_events": sum(row.get("decision") == "NEW_EVENT" for row in results),
        "matched": sum(row.get("decision") == "MATCHED" for row in results),
        "ambiguous": sum(row.get("decision") == "AMBIGUOUS" for row in results),
        "results": results,
    }
