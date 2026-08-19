from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from agents.article_analyzer_agent import (
    ARTICLE_ANALYZER_PROMPT_VERSION,
    analyze_article,
    get_article_analyzer_model,
)
from agents.article_analyzer_schema import ArticleAnalysisOutput
from processing.article_analysis_entities import (
    ResolvedAnalysisEntities,
    resolve_analysis_entities,
)


ANALYSES_TABLE = "article_analyses"
ANALYSIS_COMPANIES_TABLE = "article_analysis_companies"
ANALYSIS_TOPICS_TABLE = "article_analysis_topics"
ARTICLE_ANALYSIS_VERSION = "v1"
ELIGIBLE_RELEVANCE_CLASSES = {
    "DIRECT_HOLDING",
    "INDUSTRY_RELEVANT",
    "WEAK_MATCH",
}


def select_articles_for_analysis(
    relevance_rows: list[dict[str, Any]],
    successful_article_ids: set[int],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be at least 1.")

    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in relevance_rows:
        article_id = row["article_id"]
        if article_id in seen:
            continue
        seen.add(article_id)
        if row.get("relevance_class") not in ELIGIBLE_RELEVANCE_CLASSES:
            continue
        if article_id in successful_article_ids:
            continue
        selected.append(row)
        if len(selected) == limit:
            break
    return selected


def load_analysis_candidates(
    supabase: Any,
    *,
    limit: int,
    article_id: int | None = None,
) -> list[dict[str, Any]]:
    query = supabase.table("article_relevance").select("*")
    if article_id is not None:
        query = query.eq("article_id", article_id)
    response = query.order("processed_at", desc=True).execute()

    success_query = (supabase.table(ANALYSES_TABLE)
        .select("article_id")
        .eq("analysis_version", ARTICLE_ANALYSIS_VERSION)
        .eq("status", "SUCCESS"))
    if article_id is not None:
        success_query = success_query.eq("article_id", article_id)
    successful = {
        row["article_id"] for row in (success_query.execute().data or [])
    }
    return select_articles_for_analysis(
        response.data or [], successful, limit=limit
    )


def load_article(supabase: Any, article_id: int) -> dict[str, Any]:
    response = (supabase.table("articles")
        .select("article_id,title,summary,published_at,url,source_id")
        .eq("article_id", article_id).limit(1).execute())
    if not response.data:
        raise RuntimeError(f"Article {article_id} was not found.")
    article = response.data[0]
    if article.get("source_id") is not None:
        source = (supabase.table("news_sources").select("source_name")
            .eq("source_id", article["source_id"]).limit(1).execute())
        if source.data:
            article["source"] = source.data[0].get("source_name")
    return article


def load_reference_catalogs(supabase: Any) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    companies = (supabase.table("companies")
        .select("company_id,canonical_name,primary_ticker")
        .eq("is_active", True).execute().data or [])
    aliases = (supabase.table("company_aliases")
        .select("company_id,alias,alias_type")
        .eq("is_active", True).execute().data or [])
    topics = (supabase.table("industry_topics")
        .select("topic_id,topic_name").execute().data or [])
    return companies, aliases, topics


def start_analysis(
    supabase: Any,
    relevance: dict[str, Any],
    *,
    model: str,
) -> int:
    row = {
        "article_id": relevance["article_id"],
        "relevance_id": relevance["relevance_id"],
        "status": "RUNNING",
        "input_type": "RSS_SUMMARY",
        "model": model,
        "prompt_version": ARTICLE_ANALYZER_PROMPT_VERSION,
        "analysis_version": ARTICLE_ANALYSIS_VERSION,
        "has_event": None,
        "event_type": None,
        "impact_direction": None,
        "impact_strength": None,
        "importance_score": None,
        "confidence": None,
        "time_horizon": None,
        "event_date": None,
        "summary": None,
        "industry_implication": None,
        "facts": [],
        "risk_factors": [],
        "error_message": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "analyzed_at": None,
    }
    response = (supabase.table(ANALYSES_TABLE)
        .upsert(row, on_conflict="article_id,analysis_version")
        .execute())
    if not response.data:
        raise RuntimeError("Could not create the article analysis run record.")
    return response.data[0]["analysis_id"]


def finish_analysis_success(
    supabase: Any,
    analysis_id: int,
    analysis: ArticleAnalysisOutput,
    resolved: ResolvedAnalysisEntities,
) -> None:
    company_rows = [
        {"analysis_id": analysis_id, "company_id": company_id,
         "company_role": "AFFECTED"}
        for company_id in resolved.company_ids
    ]
    topic_rows = [
        {"analysis_id": analysis_id, "topic_id": topic_id}
        for topic_id in resolved.topic_ids
    ]
    if company_rows:
        supabase.table(ANALYSIS_COMPANIES_TABLE).upsert(company_rows).execute()
    if topic_rows:
        supabase.table(ANALYSIS_TOPICS_TABLE).upsert(topic_rows).execute()

    values = analysis.model_dump(mode="json")
    values.pop("companies")
    values.pop("topics")
    values.update({
        "status": "SUCCESS",
        "error_message": None,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    })
    (supabase.table(ANALYSES_TABLE).update(values)
        .eq("analysis_id", analysis_id).execute())


def finish_analysis_failure(
    supabase: Any,
    analysis_id: int,
    error: Exception,
) -> None:
    (supabase.table(ANALYSES_TABLE).update({
        "status": "FAILED",
        "error_message": str(error)[:2000] or error.__class__.__name__,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("analysis_id", analysis_id).execute())


def run_article_analysis_batch(
    supabase: Any,
    *,
    limit: int,
    save: bool = False,
    article_id: int | None = None,
    model: str | None = None,
    analyzer: Callable[..., ArticleAnalysisOutput] = analyze_article,
) -> dict[str, Any]:
    selected_model = model or get_article_analyzer_model()
    candidates = load_analysis_candidates(
        supabase, limit=limit, article_id=article_id
    )
    companies, aliases, topics = load_reference_catalogs(supabase)
    results: list[dict[str, Any]] = []

    for relevance in candidates:
        analysis_id: int | None = None
        try:
            article = load_article(supabase, relevance["article_id"])
            if save:
                analysis_id = start_analysis(
                    supabase, relevance, model=selected_model
                )
            analysis = analyzer(
                article, relevance, companies, aliases, topics,
                model=selected_model,
            )
            resolved = resolve_analysis_entities(
                analysis, companies, aliases, topics
            )
            if save and analysis_id is not None:
                finish_analysis_success(
                    supabase, analysis_id, analysis, resolved
                )
            results.append({
                "article_id": relevance["article_id"],
                "analysis_id": analysis_id,
                "status": "SUCCESS",
                "analysis": analysis.model_dump(mode="json"),
                "entity_resolution": resolved.as_dict(),
            })
        except Exception as exc:
            if save and analysis_id is not None:
                finish_analysis_failure(supabase, analysis_id, exc)
            results.append({
                "article_id": relevance["article_id"],
                "analysis_id": analysis_id,
                "status": "FAILED",
                "error": str(exc),
            })

    return {
        "mode": "SAVE" if save else "PREVIEW",
        "model": selected_model,
        "prompt_version": ARTICLE_ANALYZER_PROMPT_VERSION,
        "analysis_version": ARTICLE_ANALYSIS_VERSION,
        "selected": len(candidates),
        "succeeded": sum(row["status"] == "SUCCESS" for row in results),
        "failed": sum(row["status"] == "FAILED" for row in results),
        "results": results,
    }
