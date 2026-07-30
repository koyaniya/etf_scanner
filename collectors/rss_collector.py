from __future__ import annotations

import hashlib
import html
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from time import struct_time
from typing import Any
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import Client, create_client


NEWS_SOURCES_TABLE = "news_sources"
REQUEST_TIMEOUT_SECONDS = 30
ARTICLES_TABLE = "articles"

USER_AGENT = (
    "UFO-News-Agent/1.0 "
    "(personal research project; RSS collector)"
)


@dataclass
class RSSSource:
    source_id: int
    source_name: str
    feed_url: str
    base_url: str | None
    source_tier: int
    reliability_score: float | None


@dataclass
class CollectedArticle:
    source_id: int
    source_name: str

    external_id: str | None
    title: str
    url: str
    canonical_url: str

    published_at: str | None
    author: str | None

    summary: str | None

    content_hash: str
    collected_at: str


def create_supabase_client() -> Client:
    """
    Create the Supabase client using backend credentials.
    """

    load_dotenv(override=True)

    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv(
        "SUPABASE_SERVICE_ROLE_KEY"
    )

    if not supabase_url:
        raise RuntimeError(
            "SUPABASE_URL is missing from the environment."
        )

    if not service_role_key:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is missing "
            "from the environment."
        )

    return create_client(
        supabase_url,
        service_role_key,
    )


def create_http_session() -> requests.Session:
    """
    Create a reusable HTTP session.
    """

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/rss+xml,"
                "application/atom+xml,"
                "application/xml,"
                "text/xml,"
                "*/*"
            ),
        }
    )

    return session


def get_active_rss_sources(
    supabase: Client,
) -> list[RSSSource]:
    """
    Read all active RSS sources from Supabase.
    """

    response = (
        supabase.table(NEWS_SOURCES_TABLE)
        .select(
            "source_id,"
            "source_name,"
            "feed_url,"
            "base_url,"
            "source_tier,"
            "reliability_score"
        )
        .eq("is_active", True)
        .eq("collection_method", "RSS")
        .not_.is_("feed_url", "null")
        .order("source_tier")
        .execute()
    )

    sources: list[RSSSource] = []

    for row in response.data or []:
        feed_url = normalize_optional_text(
            row.get("feed_url")
        )

        if not feed_url:
            continue

        reliability_score = row.get(
            "reliability_score"
        )

        sources.append(
            RSSSource(
                source_id=row["source_id"],
                source_name=row["source_name"],
                feed_url=feed_url,
                base_url=normalize_optional_text(
                    row.get("base_url")
                ),
                source_tier=row["source_tier"],
                reliability_score=(
                    float(reliability_score)
                    if reliability_score is not None
                    else None
                ),
            )
        )

    return sources


def normalize_optional_text(
    value: Any,
) -> str | None:
    """
    Convert an optional value into clean text.
    """

    if value is None:
        return None

    cleaned = str(value).strip()

    return cleaned or None


def normalize_whitespace(text: str) -> str:
    """
    Convert repeated whitespace into single spaces.
    """

    return re.sub(r"\s+", " ", text).strip()


def clean_html_text(
    value: Any,
) -> str | None:
    """
    Remove HTML tags and decode HTML entities.
    """

    raw_text = normalize_optional_text(value)

    if not raw_text:
        return None

    decoded = html.unescape(raw_text)

    soup = BeautifulSoup(decoded, "html.parser")

    clean_text = soup.get_text(
        separator=" ",
        strip=True,
    )

    clean_text = normalize_whitespace(clean_text)

    return clean_text or None


def get_entry_value(
    entry: Any,
    key: str,
) -> Any:
    """
    Safely read a value from a feedparser entry.
    """

    if hasattr(entry, "get"):
        return entry.get(key)

    return None


def extract_title(
    entry: Any,
) -> str | None:
    """
    Extract and clean an RSS entry title.
    """

    return clean_html_text(
        get_entry_value(entry, "title")
    )


def extract_url(
    entry: Any,
    source: RSSSource,
) -> str | None:
    """
    Extract the article URL.

    Some feeds provide relative links, so urljoin is used
    with the feed or website base URL.
    """

    link = normalize_optional_text(
        get_entry_value(entry, "link")
    )

    if not link:
        links = get_entry_value(entry, "links") or []

        for candidate in links:
            if not isinstance(candidate, dict):
                continue

            relationship = candidate.get("rel", "alternate")
            href = normalize_optional_text(
                candidate.get("href")
            )

            if href and relationship == "alternate":
                link = href
                break

    if not link:
        return None

    base_url = source.base_url or source.feed_url

    return urljoin(base_url, link)


def extract_external_id(
    entry: Any,
) -> str | None:
    """
    Extract the feed's own article identifier.
    """

    return normalize_optional_text(
        get_entry_value(entry, "id")
        or get_entry_value(entry, "guid")
    )


def extract_author(
    entry: Any,
) -> str | None:
    """
    Extract an article author when available.
    """

    author = normalize_optional_text(
        get_entry_value(entry, "author")
    )

    if author:
        return author

    author_detail = get_entry_value(
        entry,
        "author_detail",
    )

    if isinstance(author_detail, dict):
        return normalize_optional_text(
            author_detail.get("name")
        )

    return None


def extract_summary(
    entry: Any,
) -> str | None:
    """
    Extract the best summary or content preview.
    """

    summary = clean_html_text(
        get_entry_value(entry, "summary")
        or get_entry_value(entry, "description")
    )

    if summary:
        return summary

    contents = get_entry_value(entry, "content") or []

    if contents:
        first_content = contents[0]

        if isinstance(first_content, dict):
            return clean_html_text(
                first_content.get("value")
            )

    return None


def struct_time_to_iso(
    value: struct_time | None,
) -> str | None:
    """
    Convert a parsed feed date to UTC ISO format.
    """

    if value is None:
        return None

    try:
        date_value = datetime(
            year=value.tm_year,
            month=value.tm_mon,
            day=value.tm_mday,
            hour=value.tm_hour,
            minute=value.tm_min,
            second=value.tm_sec,
            tzinfo=timezone.utc,
        )

        return date_value.isoformat()

    except (TypeError, ValueError):
        return None


def extract_published_at(
    entry: Any,
) -> str | None:
    """
    Extract the publication or update date.

    Feedparser commonly provides parsed time tuples under:
    - published_parsed
    - updated_parsed
    """

    published_parsed = get_entry_value(
        entry,
        "published_parsed",
    )

    if published_parsed:
        return struct_time_to_iso(published_parsed)

    updated_parsed = get_entry_value(
        entry,
        "updated_parsed",
    )

    if updated_parsed:
        return struct_time_to_iso(updated_parsed)

    return None


def normalize_title_for_hash(
    title: str,
) -> str:
    """
    Normalize a title before generating its hash.
    """

    normalized = title.lower()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = normalize_whitespace(normalized)

    return normalized


def create_content_hash(
    title: str,
    url: str,
) -> str:
    """
    Create a stable identifier for exact duplicate checking.

    The current prototype uses normalized title plus URL.
    Later, article body text can be included.
    """

    normalized_title = normalize_title_for_hash(title)

    hash_input = f"{normalized_title}|{url}"

    return hashlib.sha256(
        hash_input.encode("utf-8")
    ).hexdigest()


def parse_feed_entry(
    entry: Any,
    source: RSSSource,
) -> CollectedArticle | None:
    """
    Convert one RSS entry into a normalized article object.
    """

    title = extract_title(entry)
    url = extract_url(entry, source)

    if not title or not url:
        return None

    collected_at = datetime.now(
        timezone.utc
    ).isoformat()

    return CollectedArticle(
        source_id=source.source_id,
        source_name=source.source_name,
        external_id=extract_external_id(entry),
        title=title,
        url=url,
        canonical_url=url,
        published_at=extract_published_at(entry),
        author=extract_author(entry),
        summary=extract_summary(entry),
        content_hash=create_content_hash(
            title=title,
            url=url,
        ),
        collected_at=collected_at,
    )


def download_feed(
    session: requests.Session,
    source: RSSSource,
) -> bytes:
    """
    Download one RSS feed using requests.
    """

    response = session.get(
        source.feed_url,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            f"Empty RSS response from {source.feed_url}"
        )

    return response.content


def collect_source(
    session: requests.Session,
    source: RSSSource,
) -> list[CollectedArticle]:
    """
    Download and parse one source.
    """

    print(
        f"\nCollecting: {source.source_name}\n"
        f"Feed: {source.feed_url}"
    )

    raw_feed = download_feed(
        session=session,
        source=source,
    )

    parsed_feed = feedparser.parse(raw_feed)

    if parsed_feed.bozo:
        bozo_exception = getattr(
            parsed_feed,
            "bozo_exception",
            None,
        )

        print(
            "Warning: feedparser reported a feed issue: "
            f"{bozo_exception}"
        )

    articles: list[CollectedArticle] = []

    for entry in parsed_feed.entries:
        article = parse_feed_entry(
            entry=entry,
            source=source,
        )

        if article:
            articles.append(article)

    print(
        f"Collected {len(articles)} valid entries "
        f"from {source.source_name}."
    )

    return articles


def remove_exact_duplicates(
    articles: list[CollectedArticle],
) -> list[CollectedArticle]:
    """
    Remove duplicate records found during the same run.
    """

    unique_articles: list[CollectedArticle] = []
    seen_hashes: set[str] = set()

    for article in articles:
        if article.content_hash in seen_hashes:
            continue

        seen_hashes.add(article.content_hash)
        unique_articles.append(article)

    return unique_articles


def update_source_collection_time(
    supabase: Client,
    source_id: int,
) -> None:
    """
    Record a successful collection attempt.
    """

    (
        supabase.table(NEWS_SOURCES_TABLE)
        .update(
            {
                "last_collected_at": datetime.now(
                    timezone.utc
                ).isoformat()
            }
        )
        .eq("source_id", source_id)
        .execute()
    )


def print_articles(
    articles: list[CollectedArticle],
    limit: int = 10,
) -> None:
    """
    Print a small preview of collected articles.
    """

    print(
        f"\nTotal unique articles collected: "
        f"{len(articles)}"
    )

    for index, article in enumerate(
        articles[:limit],
        start=1,
    ):
        print("\n" + "=" * 80)
        print(f"{index}. {article.title}")
        print(f"Source: {article.source_name}")
        print(f"Published: {article.published_at}")
        print(f"URL: {article.url}")

        if article.summary:
            preview = article.summary[:300]

            if len(article.summary) > 300:
                preview += "..."

            print(f"Summary: {preview}")


def collect_all_rss_sources(
    supabase: Client,
    session: requests.Session,
) -> list[CollectedArticle]:
    """
    Collect all active RSS sources.

    A failure in one feed does not stop the other feeds.
    """

    sources = get_active_rss_sources(supabase)

    if not sources:
        print(
            "No active RSS sources were found "
            "in public.news_sources."
        )
        return []

    print(f"Found {len(sources)} active RSS sources.")

    all_articles: list[CollectedArticle] = []

    for source in sources:
        try:
            source_articles = collect_source(
                session=session,
                source=source,
            )

            all_articles.extend(source_articles)

            update_source_collection_time(
                supabase=supabase,
                source_id=source.source_id,
            )

        except requests.RequestException as exc:
            print(
                f"HTTP error for {source.source_name}: "
                f"{exc}"
            )

        except Exception as exc:
            print(
                f"Collection error for "
                f"{source.source_name}: "
                f"{type(exc).__name__}: {exc}"
            )

    return remove_exact_duplicates(all_articles)


def save_articles(
    supabase: Client,
    articles: list[CollectedArticle],
) -> int:
    """
    Save collected RSS articles into Supabase.

    Existing rows with the same URL are updated instead of duplicated.
    """

    if not articles:
        print("No articles to save.")
        return 0

    records = []

    for article in articles:
        record = asdict(article)

        # source_name is useful for printing,
        # but it is not a column in the articles table.
        record.pop("source_name", None)

        records.append(record)

    response = (
        supabase.table(ARTICLES_TABLE)
        .upsert(
            records,
            on_conflict="url",
        )
        .execute()
    )

    saved_count = len(response.data or records)

    print(f"Saved or updated {saved_count} articles.")

    return saved_count


def main() -> None:
    supabase = create_supabase_client()
    session = create_http_session()

    articles = collect_all_rss_sources(
        supabase=supabase,
        session=session,
    )

    print_articles(
        articles=articles,
        limit=10,
    )

    save_articles(
        supabase=supabase,
        articles=articles,
    )


if __name__ == "__main__":
    main()