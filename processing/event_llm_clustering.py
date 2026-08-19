from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from agents.event_cluster_agent import (
    EVENT_CLUSTER_PROMPT_VERSION,
    EventClusterDecision,
    compare_ambiguous_event,
    get_event_cluster_model,
)
from processing.event_candidates import find_event_candidates
from processing.event_clustering import (
    CLUSTERING_VERSION,
    create_canonical_event,
    link_analysis_to_event,
    update_event_publication_window,
)


def load_ambiguous_clustering_runs(
    supabase: Any,
    *,
    limit: int,
    analysis_id: int | None = None,
) -> list[dict[str, Any]]:
    query = (supabase.table("event_clustering_runs")
        .select("clustering_run_id,analysis_id,status")
        .eq("clustering_version", CLUSTERING_VERSION)
        .eq("decision", "AMBIGUOUS"))
    if analysis_id is not None:
        query = query.eq("analysis_id", analysis_id)
    return query.order("started_at").limit(limit).execute().data or []


def mark_llm_run_started(
    supabase: Any,
    run_id: int,
    model: str,
) -> None:
    (supabase.table("event_clustering_runs").update({
        "status": "RUNNING",
        "decision_method": "LLM",
        "model": model,
        "prompt_version": EVENT_CLUSTER_PROMPT_VERSION,
        "error_message": None,
        "completed_at": None,
    }).eq("clustering_run_id", run_id).execute())


def finish_llm_run(
    supabase: Any,
    run_id: int,
    result: EventClusterDecision,
    *,
    selected_event_id: int,
    candidate_count: int,
    best_similarity: float | None,
    model: str,
) -> None:
    (supabase.table("event_clustering_runs").update({
        "status": "SUCCESS",
        "decision": result.decision,
        "decision_method": "LLM",
        "selected_event_id": selected_event_id,
        "candidate_count": candidate_count,
        "best_similarity": best_similarity,
        "model": model,
        "prompt_version": EVENT_CLUSTER_PROMPT_VERSION,
        "decision_reason": result.reason,
        "error_message": None,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("clustering_run_id", run_id).execute())


def fail_llm_run(supabase: Any, run_id: int, error: Exception) -> None:
    (supabase.table("event_clustering_runs").update({
        "status": "FAILED",
        "decision": "AMBIGUOUS",
        "decision_method": "LLM",
        "selected_event_id": None,
        "error_message": str(error)[:2000] or error.__class__.__name__,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("clustering_run_id", run_id).execute())


def run_llm_event_clustering_batch(
    supabase: Any,
    *,
    limit: int,
    save: bool = False,
    analysis_id: int | None = None,
    window_days: int | None = None,
    model: str | None = None,
    comparator: Callable[..., EventClusterDecision] = compare_ambiguous_event,
) -> dict[str, Any]:
    selected_model = model or get_event_cluster_model()
    runs = load_ambiguous_clustering_runs(
        supabase, limit=limit, analysis_id=analysis_id
    )
    results: list[dict[str, Any]] = []

    for run in runs:
        run_id = run["clustering_run_id"]
        try:
            analysis, candidates = find_event_candidates(
                supabase, run["analysis_id"], window_days=window_days
            )
            if save:
                mark_llm_run_started(supabase, run_id, selected_model)
            if not candidates:
                comparison = EventClusterDecision(
                    decision="NEW_EVENT",
                    selected_event_id=None,
                    confidence=1,
                    reason="No active candidate remains; create a new event.",
                    matching_evidence=[],
                    conflicting_evidence=[],
                )
            else:
                comparison = comparator(
                    analysis, candidates, model=selected_model
                )

            selected_event_id = comparison.selected_event_id
            if save:
                if comparison.decision == "NEW_EVENT":
                    selected_event_id = create_canonical_event(supabase, analysis)
                    match_method = "NEW_EVENT"
                    match_score = candidates[0].similarity_score if candidates else None
                else:
                    if selected_event_id is None:
                        raise RuntimeError("Matched LLM decision has no event ID.")
                    match_method = "LLM"
                    match_score = next(
                        candidate.similarity_score for candidate in candidates
                        if candidate.event_id == selected_event_id
                    )
                link_analysis_to_event(
                    supabase, analysis, selected_event_id,
                    match_method=match_method,
                    match_score=match_score,
                    reason=comparison.reason,
                )
                if comparison.decision == "MATCHED":
                    update_event_publication_window(
                        supabase, selected_event_id, analysis
                    )
                best_similarity = (
                    candidates[0].similarity_score if candidates else None
                )
                # Preserve the actual selected model even when the environment differs.
                finish_llm_run(
                    supabase, run_id, comparison,
                    selected_event_id=selected_event_id,
                    candidate_count=len(candidates),
                    best_similarity=best_similarity,
                    model=selected_model,
                )
            results.append({
                "analysis_id": run["analysis_id"],
                "clustering_run_id": run_id,
                "status": "SUCCESS",
                "decision": comparison.model_dump(mode="json"),
                "selected_event_id": selected_event_id,
                "candidate_count": len(candidates),
            })
        except Exception as exc:
            if save:
                fail_llm_run(supabase, run_id, exc)
            results.append({
                "analysis_id": run["analysis_id"],
                "clustering_run_id": run_id,
                "status": "FAILED",
                "error": str(exc),
            })

    return {
        "mode": "SAVE" if save else "PREVIEW",
        "model": selected_model,
        "prompt_version": EVENT_CLUSTER_PROMPT_VERSION,
        "selected": len(runs),
        "succeeded": sum(row["status"] == "SUCCESS" for row in results),
        "failed": sum(row["status"] == "FAILED" for row in results),
        "results": results,
    }
