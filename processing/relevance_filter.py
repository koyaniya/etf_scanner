from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client


FILTER_VERSION = "v1"

ARTICLES_TABLE = "articles"
RELEVANCE_TABLE = "article_relevance"
COMPANIES_TABLE = "companies"
ALIASES_TABLE = "company_aliases"
TOPICS_TABLE = "industry_topics"
TOPIC_KEYWORDS_TABLE = "topic_keywords"


# -----------------------------
# Scoring configuration
# -----------------------------

DIRECT_COMPANY_SCORE = 50

ALIAS_SCORES = {
    "OFFICIAL_NAME": 50,
    "SHORT_NAME": 50,
    "TICKER": 50,
    "FORMER_NAME": 30,
    "PRODUCT": 30,
    "BRAND": 30,
    "SUBSIDIARY": 35,
    "MANUAL": 25,
}


@dataclass
class CompanyAlias:
    company_id: int
    company_name: str
    alias: str
    alias_type: str
    score: int


@dataclass
class TopicKeyword:
    topic_id: int
    topic_name: str
    keyword: str
    keyword_type: str
    weight: int


@dataclass
class RelevanceResult:
    article_id: int

    relevance_class: str
    relevance_score: int

    company_match_score: int
    topic_match_score: int
    event_match_score: int

    matched_company_ids: list[int]
    matched_aliases: list[str]

    matched_topic_ids: list[int]
    matched_keywords: list[str]

    needs_llm_review: bool


def create_supabase_client() -> Client:
    load_dotenv(override=True)

    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv(
        "SUPABASE_SERVICE_ROLE_KEY"
    )

    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is missing.")

    if not service_role_key:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is missing."
        )

    return create_client(
        supabase_url,
        service_role_key,
    )


def normalize_text(text: str | None) -> str:
    """
    Normalize text for matching.

    Example:
        "Rocket   Lab, Inc."
        -> "rocket lab inc"
    """

    if not text:
        return ""

    text = text.lower()

    # Replace punctuation with spaces.
    text = re.sub(
        r"[^\w\s\-]",
        " ",
        text,
        flags=re.UNICODE,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def build_article_text(
    article: dict[str, Any],
) -> str:
    """
    Combine title and RSS summary.

    We deliberately give the title more importance later,
    but first we create one searchable text block.
    """

    title = article.get("title") or ""
    summary = article.get("summary") or ""

    return normalize_text(
        f"{title} {summary}"
    )


def load_unprocessed_articles(
    supabase: Client,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Load articles that do not yet have a relevance result
    for FILTER_VERSION.
    """

    articles_response = (
        supabase.table(ARTICLES_TABLE)
        .select(
            "article_id,"
            "title,"
            "summary,"
            "published_at,"
            "source_id"
        )
        .order("published_at", desc=True)
        .limit(limit)
        .execute()
    )

    articles = articles_response.data or []

    if not articles:
        return []

    relevance_response = (
        supabase.table(RELEVANCE_TABLE)
        .select("article_id")
        .eq("filter_version", FILTER_VERSION)
        .execute()
    )

    processed_article_ids = {
        row["article_id"]
        for row in relevance_response.data or []
    }

    return [
        article
        for article in articles
        if article["article_id"]
        not in processed_article_ids
    ]


def load_company_aliases(
    supabase: Client,
) -> list[CompanyAlias]:
    """
    Load active aliases together with company names.
    """

    aliases_response = (
        supabase.table(ALIASES_TABLE)
        .select(
            "company_id,"
            "alias,"
            "alias_type,"
            "companies(canonical_name)"
        )
        .eq("is_active", True)
        .execute()
    )

    aliases: list[CompanyAlias] = []

    for row in aliases_response.data or []:
        alias = row.get("alias")

        if not alias:
            continue

        company_data = row.get("companies") or {}

        alias_type = row.get(
            "alias_type",
            "MANUAL",
        )

        score = ALIAS_SCORES.get(
            alias_type,
            25,
        )

        aliases.append(
            CompanyAlias(
                company_id=row["company_id"],
                company_name=company_data.get(
                    "canonical_name",
                    "",
                ),
                alias=alias,
                alias_type=alias_type,
                score=score,
            )
        )

    return aliases


def load_topic_keywords(
    supabase: Client,
) -> list[TopicKeyword]:
    """
    Load active topic keywords together with topic names.
    """

    response = (
        supabase.table(TOPIC_KEYWORDS_TABLE)
        .select(
            "topic_id,"
            "keyword,"
            "keyword_type,"
            "weight,"
            "industry_topics(topic_name)"
        )
        .eq("is_active", True)
        .execute()
    )

    keywords: list[TopicKeyword] = []

    for row in response.data or []:
        keyword = row.get("keyword")

        if not keyword:
            continue

        topic_data = (
            row.get("industry_topics")
            or {}
        )

        keywords.append(
            TopicKeyword(
                topic_id=row["topic_id"],
                topic_name=topic_data.get(
                    "topic_name",
                    "",
                ),
                keyword=keyword,
                keyword_type=row.get(
                    "keyword_type",
                    "PHRASE",
                ),
                weight=int(
                    row.get("weight", 10)
                ),
            )
        )

    return keywords


def contains_phrase(
    normalized_text: str,
    phrase: str,
) -> bool:
    """
    Check whether a normalized phrase exists.

    Word boundaries reduce false matches.

    Example:
        ticker "PL" should not match "planet".
    """

    normalized_phrase = normalize_text(phrase)

    if not normalized_phrase:
        return False

    pattern = (
        r"(?<!\w)"
        + re.escape(normalized_phrase)
        + r"(?!\w)"
    )

    return bool(
        re.search(
            pattern,
            normalized_text,
            flags=re.IGNORECASE,
        )
    )


def match_companies(
    article_text: str,
    aliases: list[CompanyAlias],
) -> tuple[
    int,
    list[int],
    list[str],
]:
    """
    Find company aliases in the article.

    Only the highest alias score per company contributes
    to company_match_score.

    This prevents:
        Rocket Lab + RKLB + Rocket Lab USA
    from becoming 150 points for the same company.
    """

    company_scores: dict[int, int] = {}

    matched_aliases: list[str] = []

    for alias in aliases:
        if not contains_phrase(
            article_text,
            alias.alias,
        ):
            continue

        matched_aliases.append(alias.alias)

        existing_score = company_scores.get(
            alias.company_id,
            0,
        )

        company_scores[alias.company_id] = max(
            existing_score,
            alias.score,
        )

    matched_company_ids = sorted(
        company_scores.keys()
    )

    company_match_score = sum(
        company_scores.values()
    )

    return (
        company_match_score,
        matched_company_ids,
        sorted(set(matched_aliases)),
    )


def match_topics(
    article_text: str,
    keywords: list[TopicKeyword],
) -> tuple[
    int,
    list[int],
    list[str],
]:
    """
    Match topic keywords.

    For each topic, only the strongest keyword contributes
    to the score.

    This avoids overcounting multiple similar phrases from
    one topic.
    """

    topic_scores: dict[int, int] = {}

    matched_keywords: list[str] = []

    for keyword in keywords:
        if not contains_phrase(
            article_text,
            keyword.keyword,
        ):
            continue

        matched_keywords.append(
            keyword.keyword
        )

        current_score = topic_scores.get(
            keyword.topic_id,
            0,
        )

        topic_scores[keyword.topic_id] = max(
            current_score,
            keyword.weight,
        )

    matched_topic_ids = sorted(
        topic_scores.keys()
    )

    topic_match_score = sum(
        topic_scores.values()
    )

    return (
        topic_match_score,
        matched_topic_ids,
        sorted(set(matched_keywords)),
    )


def classify_relevance(
    *,
    company_match_score: int,
    topic_match_score: int,
    event_match_score: int,
    matched_company_ids: list[int],
) -> tuple[str, int, bool]:
    """
    Convert component scores into the final relevance class.

    Important:
    A direct company match overrides the numerical threshold.
    """

    total_score = (
        company_match_score
        + topic_match_score
        + event_match_score
    )

    if matched_company_ids:
        relevance_class = "DIRECT_HOLDING"
        needs_llm_review = True

    elif total_score >= 40:
        relevance_class = "INDUSTRY_RELEVANT"
        needs_llm_review = True

    elif total_score >= 20:
        relevance_class = "WEAK_MATCH"
        needs_llm_review = True

    else:
        relevance_class = "IRRELEVANT"
        needs_llm_review = False

    return (
        relevance_class,
        total_score,
        needs_llm_review,
    )


def evaluate_article(
    article: dict[str, Any],
    aliases: list[CompanyAlias],
    keywords: list[TopicKeyword],
) -> RelevanceResult:
    """
    Evaluate one article.
    """

    article_text = build_article_text(
        article
    )

    (
        company_match_score,
        matched_company_ids,
        matched_aliases,
    ) = match_companies(
        article_text,
        aliases,
    )

    (
        topic_match_score,
        matched_topic_ids,
        matched_keywords,
    ) = match_topics(
        article_text,
        keywords,
    )

    # We will implement event detection in the next version.
    event_match_score = 0

    (
        relevance_class,
        relevance_score,
        needs_llm_review,
    ) = classify_relevance(
        company_match_score=company_match_score,
        topic_match_score=topic_match_score,
        event_match_score=event_match_score,
        matched_company_ids=matched_company_ids,
    )

    return RelevanceResult(
        article_id=article["article_id"],
        relevance_class=relevance_class,
        relevance_score=relevance_score,
        company_match_score=company_match_score,
        topic_match_score=topic_match_score,
        event_match_score=event_match_score,
        matched_company_ids=matched_company_ids,
        matched_aliases=matched_aliases,
        matched_topic_ids=matched_topic_ids,
        matched_keywords=matched_keywords,
        needs_llm_review=needs_llm_review,
    )


def save_results(
    supabase: Client,
    results: list[RelevanceResult],
) -> None:
    """
    Save relevance results to Supabase.
    """

    if not results:
        print("No relevance results to save.")
        return

    records = []

    for result in results:
        records.append(
            {
                "article_id":
                    result.article_id,

                "relevance_class":
                    result.relevance_class,

                "relevance_score":
                    result.relevance_score,

                "company_match_score":
                    result.company_match_score,

                "topic_match_score":
                    result.topic_match_score,

                "event_match_score":
                    result.event_match_score,

                "matched_company_ids":
                    result.matched_company_ids,

                "matched_aliases":
                    result.matched_aliases,

                "matched_topic_ids":
                    result.matched_topic_ids,

                "matched_keywords":
                    result.matched_keywords,

                "needs_llm_review":
                    result.needs_llm_review,

                "filter_method":
                    "RULE_BASED",

                "filter_version":
                    FILTER_VERSION,
            }
        )

    (
        supabase.table(RELEVANCE_TABLE)
        .upsert(
            records,
            on_conflict=(
                "article_id,filter_version"
            ),
        )
        .execute()
    )

    print(
        f"Saved {len(records)} relevance results."
    )


def print_summary(
    results: list[RelevanceResult],
) -> None:
    """
    Print simple statistics after filtering.
    """

    counts = {
        "DIRECT_HOLDING": 0,
        "INDUSTRY_RELEVANT": 0,
        "WEAK_MATCH": 0,
        "IRRELEVANT": 0,
    }

    for result in results:
        counts[result.relevance_class] += 1

    print("\nRelevance results:")
    print(
        f"DIRECT_HOLDING: "
        f"{counts['DIRECT_HOLDING']}"
    )
    print(
        f"INDUSTRY_RELEVANT: "
        f"{counts['INDUSTRY_RELEVANT']}"
    )
    print(
        f"WEAK_MATCH: "
        f"{counts['WEAK_MATCH']}"
    )
    print(
        f"IRRELEVANT: "
        f"{counts['IRRELEVANT']}"
    )


def main() -> None:
    supabase = create_supabase_client()

    print("Loading company aliases...")
    aliases = load_company_aliases(
        supabase
    )

    print(
        f"Loaded {len(aliases)} "
        "company aliases."
    )

    print("Loading topic keywords...")
    keywords = load_topic_keywords(
        supabase
    )

    print(
        f"Loaded {len(keywords)} "
        "topic keywords."
    )

    print("Loading unprocessed articles...")
    articles = load_unprocessed_articles(
        supabase
    )

    print(
        f"Found {len(articles)} "
        "articles to process."
    )

    if not articles:
        return

    results: list[RelevanceResult] = []

    for article in articles:
        result = evaluate_article(
            article=article,
            aliases=aliases,
            keywords=keywords,
        )

        results.append(result)

        print(
            f"[{result.relevance_class:18}] "
            f"{result.relevance_score:3} | "
            f"{article['title']}"
        )

    save_results(
        supabase=supabase,
        results=results,
    )

    print_summary(results)


if __name__ == "__main__":
    main()