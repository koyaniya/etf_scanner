from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from agents.article_analyzer_schema import ArticleAnalysisOutput
from collectors.company_sync import normalize_alias_key


@dataclass(frozen=True)
class ResolvedAnalysisEntities:
    company_ids: list[int]
    topic_ids: list[int]
    unresolved_companies: list[str]
    ambiguous_companies: list[str]
    unresolved_topics: list[str]
    ambiguous_topics: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def add_index_value(
    index: dict[str, set[int]],
    name: str | None,
    entity_id: int,
) -> None:
    key = normalize_alias_key(name)
    if key:
        index.setdefault(key, set()).add(entity_id)


def build_company_name_index(
    companies: list[dict[str, Any]],
    aliases: list[dict[str, Any]],
) -> dict[str, set[int]]:
    index: dict[str, set[int]] = {}

    for company in companies:
        company_id = company["company_id"]
        add_index_value(index, company.get("canonical_name"), company_id)
        add_index_value(index, company.get("primary_ticker"), company_id)

    for alias in aliases:
        add_index_value(index, alias.get("alias"), alias["company_id"])

    return index


def build_topic_name_index(
    topics: list[dict[str, Any]],
) -> dict[str, set[int]]:
    index: dict[str, set[int]] = {}
    for topic in topics:
        add_index_value(index, topic.get("topic_name"), topic["topic_id"])
    return index


def resolve_names(
    names: list[str],
    index: dict[str, set[int]],
) -> tuple[list[int], list[str], list[str]]:
    resolved_ids: set[int] = set()
    unresolved: list[str] = []
    ambiguous: list[str] = []

    for name in names:
        matches = index.get(normalize_alias_key(name), set())
        if len(matches) == 1:
            resolved_ids.update(matches)
        elif not matches:
            unresolved.append(name)
        else:
            ambiguous.append(name)

    return sorted(resolved_ids), unresolved, ambiguous


def resolve_analysis_entities(
    analysis: ArticleAnalysisOutput,
    companies: list[dict[str, Any]],
    aliases: list[dict[str, Any]],
    topics: list[dict[str, Any]],
) -> ResolvedAnalysisEntities:
    """Map model-returned names only when they identify one known entity."""

    company_ids, unresolved_companies, ambiguous_companies = resolve_names(
        analysis.companies,
        build_company_name_index(companies, aliases),
    )
    topic_ids, unresolved_topics, ambiguous_topics = resolve_names(
        analysis.topics,
        build_topic_name_index(topics),
    )

    return ResolvedAnalysisEntities(
        company_ids=company_ids,
        topic_ids=topic_ids,
        unresolved_companies=unresolved_companies,
        ambiguous_companies=ambiguous_companies,
        unresolved_topics=unresolved_topics,
        ambiguous_topics=ambiguous_topics,
    )
