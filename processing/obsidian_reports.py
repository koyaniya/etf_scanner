from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from agents.daily_summary_agent import (
    DAILY_SUMMARY_PROMPT_VERSION,
    DailySummary,
    get_daily_summary_model,
    summarize_daily_events,
)


SEOUL = ZoneInfo("Asia/Seoul")
VAULT_DIR = Path("daily_summaries")
REPORT_CUTOFF_TIME = time(hour=8, minute=30)


def _query_rows(supabase: Any, table: str, columns: str) -> list[dict[str, Any]]:
    return supabase.table(table).select(columns).execute().data or []


def load_vault_data(supabase: Any) -> dict[str, list[dict[str, Any]]]:
    return {
        "events": _query_rows(supabase, "events", "*"),
        "event_articles": _query_rows(
            supabase, "event_articles", "event_id,analysis_id"
        ),
        "analyses": _query_rows(
            supabase, "article_analyses", "analysis_id,article_id"
        ),
        "articles": _query_rows(
            supabase,
            "articles",
            "article_id,title,url,published_at",
        ),
        "event_companies": _query_rows(
            supabase, "event_companies", "event_id,company_id,company_role"
        ),
        "companies": _query_rows(
            supabase, "companies", "company_id,canonical_name,primary_ticker"
        ),
        "event_topics": _query_rows(
            supabase, "event_topics", "event_id,topic_id"
        ),
        "topics": _query_rows(
            supabase, "industry_topics", "topic_id,topic_name"
        ),
    }


def _group(rows: list[dict[str, Any]], key: str) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if value is not None:
            grouped[int(value)].append(row)
    return grouped


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _frontmatter(values: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in values.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {_yaml_scalar(item)}" for item in value)
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    return "\n".join([*lines, "---"])


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp lacks a timezone: {value}")
    return parsed


def report_window(summary_date: date) -> tuple[datetime, datetime]:
    """Return the rolling 24-hour window ending at 08:30 Asia/Seoul."""

    end = datetime.combine(summary_date, REPORT_CUTOFF_TIME, tzinfo=SEOUL)
    return end - timedelta(hours=24), end


def _article_is_in_window(
    article: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
) -> bool:
    published = _parse_timestamp(article.get("published_at"))
    if published is None:
        return False
    published_seoul = published.astimezone(SEOUL)
    return window_start <= published_seoul < window_end


def _format_publication_time(value: str | None) -> str | None:
    published = _parse_timestamp(value)
    if published is None:
        return None
    return published.astimezone(SEOUL).strftime("%Y-%m-%d %H:%M KST")


def _event_link(event: dict[str, Any]) -> str:
    title = str(event.get("canonical_title") or f"Event {event['event_id']}")
    title = title.replace("|", "-").replace("]", "")
    return f"[[event-{event['event_id']}|{title}]]"


def _company_references(
    data: dict[str, list[dict[str, Any]]]
) -> dict[int, list[str]]:
    company_by_id = {
        int(row["company_id"]): row for row in data["companies"]
    }
    references: dict[int, list[str]] = defaultdict(list)
    for link in data["event_companies"]:
        company_id = int(link["company_id"])
        company = company_by_id.get(company_id)
        if company:
            references[int(link["event_id"])].append(
                f"{company['canonical_name']} [company_id: {company_id}]"
            )
    return {
        event_id: sorted(set(values))
        for event_id, values in references.items()
    }


def _bullet_section(title: str, values: list[str]) -> str:
    if not values:
        return ""
    return f"\n## {title}\n\n" + "\n".join(f"- {value}" for value in values) + "\n"


def build_event_documents(data: dict[str, list[dict[str, Any]]]) -> dict[int, str]:
    events = {int(row["event_id"]): row for row in data["events"]}
    topic_by_id = {int(row["topic_id"]): row for row in data["topics"]}
    analysis_by_id = {int(row["analysis_id"]): row for row in data["analyses"]}
    article_by_id = {int(row["article_id"]): row for row in data["articles"]}
    event_articles = _group(data["event_articles"], "event_id")
    event_companies = _group(data["event_companies"], "event_id")
    event_topics = _group(data["event_topics"], "event_id")

    company_ids_by_event = {
        event_id: {int(row["company_id"]) for row in rows}
        for event_id, rows in event_companies.items()
    }
    topic_ids_by_event = {
        event_id: {int(row["topic_id"]) for row in rows}
        for event_id, rows in event_topics.items()
    }
    company_references = _company_references(data)
    documents: dict[int, str] = {}

    for event_id, event in sorted(events.items()):
        if event.get("status") == "MERGED":
            target_id = event.get("merged_into_event_id")
            target = events.get(int(target_id)) if target_id is not None else None
            target_link = _event_link(target) if target else f"[[event-{target_id}]]"
            documents[event_id] = (
                _frontmatter({
                    "event_id": event_id,
                    "status": "MERGED",
                    "merged_into_event_id": target_id,
                })
                + f"\n\n# Event merged\n\nThis event is now part of {target_link}.\n"
            )
            continue

        companies = company_references.get(event_id, [])
        topics = sorted(
            topic_by_id[topic_id]["topic_name"]
            for topic_id in topic_ids_by_event.get(event_id, set())
            if topic_id in topic_by_id
        )
        sources: list[str] = []
        for link in event_articles.get(event_id, []):
            analysis = analysis_by_id.get(int(link["analysis_id"]))
            article = article_by_id.get(int(analysis["article_id"])) if analysis else None
            if not article:
                continue
            title = re.sub(r"[\[\]]", "", str(article.get("title") or "Source"))
            url = str(article.get("url") or "").replace(")", "%29")
            published = _format_publication_time(article.get("published_at"))
            suffix = f" — {published}" if published else ""
            sources.append(f"[{title}]({url}){suffix}" if url else f"{title}{suffix}")

        related: list[tuple[int, int]] = []
        if event.get("status") == "ACTIVE":
            own_companies = company_ids_by_event.get(event_id, set())
            own_topics = topic_ids_by_event.get(event_id, set())
            for other_id, other in events.items():
                if other_id == event_id or other.get("status") != "ACTIVE":
                    continue
                score = (
                    2 * len(own_companies & company_ids_by_event.get(other_id, set()))
                    + len(own_topics & topic_ids_by_event.get(other_id, set()))
                )
                if score:
                    related.append((score, other_id))
        related_links = [
            _event_link(events[other_id])
            for _, other_id in sorted(related, key=lambda item: (-item[0], item[1]))[:10]
        ]

        body = _frontmatter({
            "event_id": event_id,
            "status": event.get("status"),
            "event_type": event.get("event_type"),
            "event_date": event.get("event_date"),
            "impact_direction": event.get("impact_direction"),
            "impact_strength": event.get("impact_strength"),
            "importance_score": event.get("importance_score"),
            "confidence": event.get("confidence"),
            "time_horizon": event.get("time_horizon"),
            "companies": companies,
            "topics": topics,
            "updated_at": event.get("updated_at"),
        })
        body += f"\n\n# {event['canonical_title']}\n\n{event['canonical_summary']}\n"
        if event.get("industry_implication"):
            body += f"\n## Industry implication\n\n{event['industry_implication']}\n"
        body += _bullet_section("Key facts", event.get("facts") or [])
        body += _bullet_section("Risks", event.get("risk_factors") or [])
        body += _bullet_section("Sources", sources)
        body += _bullet_section("Related events", related_links)
        documents[event_id] = body
    return documents


def select_daily_events(
    data: dict[str, list[dict[str, Any]]], summary_date: date
) -> list[dict[str, Any]]:
    window_start, window_end = report_window(summary_date)
    window_article_ids = {
        int(row["article_id"])
        for row in data["articles"]
        if _article_is_in_window(row, window_start, window_end)
    }
    analysis_ids = {
        int(row["analysis_id"])
        for row in data["analyses"]
        if int(row["article_id"]) in window_article_ids
    }
    event_ids = {
        int(row["event_id"])
        for row in data["event_articles"]
        if int(row["analysis_id"]) in analysis_ids
    }
    return sorted(
        [
            row for row in data["events"]
            if int(row["event_id"]) in event_ids and row.get("status") == "ACTIVE"
        ],
        key=lambda row: (-int(row.get("importance_score") or 0), int(row["event_id"])),
    )


def build_daily_document(
    summary_date: date,
    events: list[dict[str, Any]],
    summary: DailySummary | None,
    *,
    model: str | None,
) -> str:
    window_start, window_end = report_window(summary_date)
    body = _frontmatter({
        "date": summary_date.isoformat(),
        "timezone": "Asia/Seoul",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "event_count": len(events),
        "model": model,
        "prompt_version": DAILY_SUMMARY_PROMPT_VERSION if summary else None,
    })
    body += f"\n\n# Space industry summary — {summary_date.isoformat()}\n"
    if summary:
        body += f"\n{summary.overview}\n"
    elif not events:
        body += (
            "\nNo canonical events were supported by articles published "
            "during this 24-hour reporting window.\n"
        )
    body += _bullet_section(
        "Events",
        [
            f"{_event_link(event)} — {str(event['impact_direction']).lower()}, "
            f"importance {event['importance_score']}"
            + (
                "; companies: " + ", ".join(event["companies"])
                if event.get("companies") else ""
            )
            for event in events
        ],
    )
    if summary:
        body += _bullet_section("Themes", summary.themes)
        body += _bullet_section("Risks and uncertainties", summary.risks)
    return body


def generate_obsidian_reports(
    supabase: Any,
    *,
    summary_date: date,
    vault_dir: Path = VAULT_DIR,
    model: str | None = None,
    summarizer: Callable[..., DailySummary] = summarize_daily_events,
) -> dict[str, Any]:
    data = load_vault_data(supabase)
    documents = build_event_documents(data)
    events = select_daily_events(data, summary_date)
    company_references = _company_references(data)
    events = [
        {**event, "companies": company_references.get(int(event["event_id"]), [])}
        for event in events
    ]
    selected_model = model or get_daily_summary_model()
    summary = summarizer(
        summary_date.isoformat(),
        [
            {key: event.get(key) for key in (
                "event_id", "canonical_title", "event_type", "impact_direction",
                "impact_strength", "importance_score", "confidence", "time_horizon",
                "event_date", "canonical_summary", "industry_implication", "facts",
                "risk_factors",
                "companies",
            )}
            for event in events
        ],
        model=selected_model,
    ) if events else None

    events_dir = vault_dir / "events"
    summaries_dir = vault_dir / "summaries"
    events_dir.mkdir(parents=True, exist_ok=True)
    summaries_dir.mkdir(parents=True, exist_ok=True)
    for event_id, document in documents.items():
        (events_dir / f"event-{event_id}.md").write_text(document, encoding="utf-8")
    summary_path = summaries_dir / f"{summary_date.isoformat()}.md"
    summary_path.write_text(
        build_daily_document(
            summary_date, events, summary, model=selected_model if summary else None
        ),
        encoding="utf-8",
    )
    return {
        "summary_date": summary_date.isoformat(),
        "events_exported": len(documents),
        "daily_events": len(events),
        "summary_path": str(summary_path),
        "model": selected_model if summary else None,
    }
