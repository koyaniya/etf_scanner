from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable

from agents.event_consolidation_agent import (
    EVENT_UPDATE_PROMPT_VERSION,
    EventConsolidation,
    consolidate_event,
    get_event_update_model,
)


EVENT_UPDATE_VERSION = "v1"
EVENT_STATE_FIELDS = (
    "canonical_title", "event_type", "impact_direction", "impact_strength",
    "importance_score", "confidence", "time_horizon", "event_date",
    "canonical_summary", "industry_implication", "facts", "risk_factors",
)


def evidence_signature(analysis_ids: list[int]) -> str:
    normalized = ",".join(str(value) for value in sorted(set(analysis_ids)))
    if not normalized:
        raise ValueError("At least one analysis ID is required.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def event_state(event: dict[str, Any]) -> dict[str, Any]:
    return {field: event.get(field) for field in EVENT_STATE_FIELDS}


def proposed_state(result: EventConsolidation) -> dict[str, Any]:
    values = result.model_dump(mode="json")
    return {field: values[field] for field in EVENT_STATE_FIELDS}


def load_pending_event_updates(
    supabase: Any,
    *,
    limit: int,
    event_id: int | None = None,
) -> list[dict[str, Any]]:
    query = supabase.table("events").select("*").eq("status", "ACTIVE")
    if event_id is not None:
        query = query.eq("event_id", event_id)
    events = query.order("event_id").execute().data or []
    if not events:
        return []

    event_ids = [row["event_id"] for row in events]
    links = (supabase.table("event_articles")
        .select("event_id,analysis_id,linked_at")
        .in_("event_id", event_ids).order("linked_at").execute().data or [])
    completed = (supabase.table("event_updates")
        .select("event_id,evidence_signature")
        .eq("update_version", EVENT_UPDATE_VERSION)
        .eq("status", "SUCCESS")
        .in_("event_id", event_ids).execute().data or [])
    completed_keys = {
        (row["event_id"], row["evidence_signature"]) for row in completed
    }
    links_by_event: dict[int, list[dict[str, Any]]] = {}
    for link in links:
        links_by_event.setdefault(link["event_id"], []).append(link)

    pending: list[dict[str, Any]] = []
    for event in events:
        event_links = links_by_event.get(event["event_id"], [])
        analysis_ids = [row["analysis_id"] for row in event_links]
        if len(set(analysis_ids)) < 2:
            continue
        signature = evidence_signature(analysis_ids)
        if (event["event_id"], signature) in completed_keys:
            continue
        pending.append({
            "event": event,
            "analysis_ids": sorted(set(analysis_ids)),
            "triggering_analysis_id": event_links[-1]["analysis_id"],
            "evidence_signature": signature,
        })
        if len(pending) == limit:
            break
    return pending


def load_event_analyses(
    supabase: Any,
    analysis_ids: list[int],
) -> list[dict[str, Any]]:
    analyses = (supabase.table("article_analyses").select(
        "analysis_id,article_id,event_type,impact_direction,impact_strength,"
        "importance_score,confidence,time_horizon,event_date,summary,"
        "industry_implication,facts,risk_factors,analyzed_at"
    ).in_("analysis_id", analysis_ids).execute().data or [])
    article_ids = [row["article_id"] for row in analyses]
    articles = (supabase.table("articles")
        .select("article_id,title,published_at,url")
        .in_("article_id", article_ids).execute().data or [])
    articles_by_id = {row["article_id"]: row for row in articles}
    for analysis in analyses:
        analysis["article"] = articles_by_id.get(analysis["article_id"], {})
    return sorted(analyses, key=lambda row: row["analysis_id"])


def start_event_update(
    supabase: Any,
    pending: dict[str, Any],
    *,
    model: str,
) -> int:
    response = (supabase.table("event_updates").upsert({
        "event_id": pending["event"]["event_id"],
        "triggering_analysis_id": pending["triggering_analysis_id"],
        "evidence_analysis_ids": pending["analysis_ids"],
        "evidence_signature": pending["evidence_signature"],
        "update_version": EVENT_UPDATE_VERSION,
        "status": "RUNNING",
        "action": None,
        "has_material_update": None,
        "before_state": event_state(pending["event"]),
        "proposed_state": None,
        "after_state": None,
        "new_information": [],
        "contradictions": [],
        "change_summary": None,
        "model": model,
        "prompt_version": EVENT_UPDATE_PROMPT_VERSION,
        "error_message": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
    }, on_conflict="event_id,evidence_signature,update_version").execute())
    if not response.data:
        raise RuntimeError("Could not create event update audit record.")
    return response.data[0]["event_update_id"]


def finish_event_update(
    supabase: Any,
    update_id: int,
    event: dict[str, Any],
    result: EventConsolidation,
) -> str:
    before = event_state(event)
    proposed = proposed_state(result)
    if result.has_material_update:
        applied = {**proposed, "updated_at": datetime.now(timezone.utc).isoformat()}
        (supabase.table("events").update(applied)
            .eq("event_id", event["event_id"]).execute())
        after = proposed
        action = "UPDATED"
    else:
        after = before
        action = "NO_CHANGE"

    (supabase.table("event_updates").update({
        "status": "SUCCESS",
        "action": action,
        "has_material_update": result.has_material_update,
        "proposed_state": proposed,
        "after_state": after,
        "new_information": result.new_information,
        "contradictions": result.contradictions,
        "change_summary": result.change_summary,
        "error_message": None,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("event_update_id", update_id).execute())
    return action


def fail_event_update(
    supabase: Any,
    update_id: int,
    error: Exception,
) -> None:
    (supabase.table("event_updates").update({
        "status": "FAILED",
        "error_message": str(error)[:2000] or error.__class__.__name__,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("event_update_id", update_id).execute())


def run_event_update_batch(
    supabase: Any,
    *,
    limit: int,
    save: bool = False,
    event_id: int | None = None,
    model: str | None = None,
    consolidator: Callable[..., EventConsolidation] = consolidate_event,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    selected_model = model or get_event_update_model()
    pending_updates = load_pending_event_updates(
        supabase, limit=limit, event_id=event_id
    )
    results: list[dict[str, Any]] = []

    for pending in pending_updates:
        update_id: int | None = None
        try:
            analyses = load_event_analyses(supabase, pending["analysis_ids"])
            if save:
                update_id = start_event_update(
                    supabase, pending, model=selected_model
                )
            consolidation = consolidator(
                event_state(pending["event"]), analyses, model=selected_model
            )
            action = (
                finish_event_update(
                    supabase, update_id, pending["event"], consolidation
                )
                if save and update_id is not None
                else ("UPDATED" if consolidation.has_material_update else "NO_CHANGE")
            )
            results.append({
                "event_id": pending["event"]["event_id"],
                "event_update_id": update_id,
                "status": "SUCCESS",
                "action": action,
                "evidence_analysis_ids": pending["analysis_ids"],
                "consolidation": consolidation.model_dump(mode="json"),
            })
        except Exception as exc:
            if save and update_id is not None:
                fail_event_update(supabase, update_id, exc)
            results.append({
                "event_id": pending["event"]["event_id"],
                "event_update_id": update_id,
                "status": "FAILED",
                "error": str(exc),
            })

    return {
        "mode": "SAVE" if save else "PREVIEW",
        "model": selected_model,
        "prompt_version": EVENT_UPDATE_PROMPT_VERSION,
        "update_version": EVENT_UPDATE_VERSION,
        "selected": len(pending_updates),
        "updated": sum(row.get("action") == "UPDATED" for row in results),
        "unchanged": sum(row.get("action") == "NO_CHANGE" for row in results),
        "failed": sum(row["status"] == "FAILED" for row in results),
        "results": results,
    }
