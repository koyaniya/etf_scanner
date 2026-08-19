from __future__ import annotations

import argparse
import json
from typing import Any

from agents.article_analyzer_agent import (
    ARTICLE_ANALYZER_PROMPT_VERSION, analyze_article, get_article_analyzer_model,
)
from processing.article_analysis_entities import resolve_analysis_entities
from processing.relevance_filter import create_supabase_client

ELIGIBLE_RELEVANCE_CLASSES = {"DIRECT_HOLDING", "INDUSTRY_RELEVANT", "WEAK_MATCH"}


def load_article(supabase: Any, article_id: int) -> dict[str, Any]:
    response = (supabase.table("articles")
        .select("article_id,title,summary,published_at,url,source_id")
        .eq("article_id", article_id).limit(1).execute())
    if not response.data:
        raise RuntimeError(f"Article {article_id} was not found.")
    article = response.data[0]
    source_id = article.get("source_id")
    if source_id is not None:
        source_response = (supabase.table("news_sources")
            .select("source_name")
            .eq("source_id", source_id).limit(1).execute())
        if source_response.data:
            article["source"] = source_response.data[0].get("source_name")
    return article


def load_latest_relevance(supabase: Any, article_id: int) -> dict[str, Any]:
    response = (supabase.table("article_relevance").select("*")
        .eq("article_id", article_id).order("processed_at", desc=True)
        .limit(1).execute())
    if not response.data:
        raise RuntimeError(f"Article {article_id} has no relevance result.")
    relevance = response.data[0]
    if relevance.get("relevance_class") not in ELIGIBLE_RELEVANCE_CLASSES:
        raise RuntimeError(
            f"Article {article_id} is {relevance.get('relevance_class')} and is "
            "not eligible for Article Analyzer."
        )
    return relevance


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview structured LLM analysis for one eligible article."
    )
    parser.add_argument("--article-id", type=int, required=True)
    parser.add_argument("--model", help="Optional OpenAI model override.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    supabase = create_supabase_client()
    article = load_article(supabase, args.article_id)
    relevance = load_latest_relevance(supabase, args.article_id)
    companies, aliases, topics = load_reference_catalogs(supabase)
    model = args.model or get_article_analyzer_model()
    analysis = analyze_article(
        article, relevance, companies, aliases, topics, model=model
    )
    resolved = resolve_analysis_entities(analysis, companies, aliases, topics)
    print(json.dumps({
        "article_id": args.article_id,
        "relevance_id": relevance.get("relevance_id"),
        "model": model,
        "prompt_version": ARTICLE_ANALYZER_PROMPT_VERSION,
        "analysis": analysis.model_dump(mode="json"),
        "entity_resolution": resolved.as_dict(),
    }, ensure_ascii=False, indent=2))
    print("Preview only. No article analysis was saved to the database.")


if __name__ == "__main__":
    main()
