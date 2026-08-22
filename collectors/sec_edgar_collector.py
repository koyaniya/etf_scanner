from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from time import sleep
from typing import Any

import requests
from supabase import Client

from collectors.rss_collector import (
    CollectedArticle,
    create_content_hash,
    create_supabase_client,
    normalize_optional_text,
    print_articles,
    save_articles,
    update_source_collection_time,
)


NEWS_SOURCES_TABLE = "news_sources"
HOLDINGS_TABLE = "etf_holdings"
ETF_TICKER = "UFO"
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_INTERVAL_SECONDS = 0.125
SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_FORMS = frozenset({"8-K", "10-K", "10-Q", "6-K", "20-F", "40-F"})


@dataclass(frozen=True)
class SECSource:
    source_id: int
    source_name: str


def create_sec_session() -> requests.Session:
    """Create a session that complies with the SEC's declared-bot policy."""

    user_agent = normalize_optional_text(os.getenv("SEC_USER_AGENT"))
    if not user_agent:
        raise RuntimeError(
            "SEC_USER_AGENT is missing. Set it to an application name and "
            "contact email, for example: UFO ETF Research contact@example.com"
        )

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }
    )
    return session


def get_active_sec_source(supabase: Client) -> SECSource | None:
    response = (
        supabase.table(NEWS_SOURCES_TABLE)
        .select("source_id,source_name")
        .eq("is_active", True)
        .eq("collection_method", "API")
        .eq("domain", "sec.gov")
        .limit(1)
        .execute()
    )
    rows = response.data or []
    if not rows:
        return None
    return SECSource(
        source_id=rows[0]["source_id"],
        source_name=rows[0]["source_name"],
    )


def get_latest_company_tickers(supabase: Client) -> set[str]:
    latest = (
        supabase.table(HOLDINGS_TABLE)
        .select("holding_date")
        .eq("etf_ticker", ETF_TICKER)
        .order("holding_date", desc=True)
        .limit(1)
        .execute()
    )
    if not latest.data:
        return set()

    rows = (
        supabase.table(HOLDINGS_TABLE)
        .select("stock_ticker")
        .eq("etf_ticker", ETF_TICKER)
        .eq("holding_date", latest.data[0]["holding_date"])
        .eq("holding_type", "COMPANY")
        .execute()
    )
    return {
        str(row["stock_ticker"]).strip().upper()
        for row in (rows.data or [])
        if normalize_optional_text(row.get("stock_ticker"))
    }


def download_json(session: requests.Session, url: str) -> dict[str, Any]:
    response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a JSON object from {url}")
    return payload


def build_ticker_cik_map(payload: dict[str, Any]) -> dict[str, str]:
    """Normalize the SEC's fields/data array into ticker -> zero-padded CIK."""

    fields = payload.get("fields") or []
    rows = payload.get("data") or []
    try:
        ticker_index = fields.index("ticker")
        cik_index = fields.index("cik")
    except ValueError as exc:
        raise RuntimeError("SEC ticker map has an unexpected schema") from exc

    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) <= max(ticker_index, cik_index):
            continue
        ticker = normalize_optional_text(row[ticker_index])
        cik = normalize_optional_text(row[cik_index])
        if ticker and cik:
            result[ticker.upper()] = cik.zfill(10)
    return result


def parse_sec_timestamp(value: Any, fallback_date: Any) -> str | None:
    raw = normalize_optional_text(value) or normalize_optional_text(fallback_date)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def filing_url(cik: str, accession_number: str, primary_document: str) -> str:
    accession_path = accession_number.replace("-", "")
    return (
        f"{SEC_ARCHIVES_URL}/{int(cik)}/{accession_path}/"
        f"{primary_document}"
    )


def parse_recent_filings(
    payload: dict[str, Any],
    source: SECSource,
    cutoff: date,
    allowed_forms: set[str] | frozenset[str],
) -> list[CollectedArticle]:
    company_name = normalize_optional_text(payload.get("name")) or "Unknown filer"
    cik = str(payload.get("cik", "")).zfill(10)
    recent = ((payload.get("filings") or {}).get("recent") or {})
    keys = (
        "accessionNumber",
        "filingDate",
        "acceptanceDateTime",
        "form",
        "primaryDocument",
        "primaryDocDescription",
    )
    columns = {key: recent.get(key) or [] for key in keys}
    articles: list[CollectedArticle] = []

    for index, accession in enumerate(columns["accessionNumber"]):
        def field(name: str) -> Any:
            values = columns[name]
            return values[index] if index < len(values) else None

        form = normalize_optional_text(field("form"))
        filing_date = normalize_optional_text(field("filingDate"))
        document = normalize_optional_text(field("primaryDocument"))
        accession = normalize_optional_text(accession)
        if not form or form not in allowed_forms or not filing_date or not document or not accession:
            continue
        try:
            if date.fromisoformat(filing_date) < cutoff:
                continue
        except ValueError:
            continue

        url = filing_url(cik, accession, document)
        description = normalize_optional_text(field("primaryDocDescription"))
        title = f"{company_name} files {form}"
        summary_parts = [f"SEC EDGAR {form} filing by {company_name}."]
        if description and description.upper() != form.upper():
            summary_parts.append(description)
        articles.append(
            CollectedArticle(
                source_id=source.source_id,
                source_name=source.source_name,
                external_id=accession,
                title=title,
                url=url,
                canonical_url=url,
                published_at=parse_sec_timestamp(
                    field("acceptanceDateTime"), filing_date
                ),
                author=company_name,
                summary=" ".join(summary_parts),
                content_hash=create_content_hash(title=title, url=url),
                collected_at=datetime.now(timezone.utc).isoformat(),
            )
        )
    return articles


def configured_forms() -> set[str]:
    raw = normalize_optional_text(os.getenv("SEC_FILING_FORMS"))
    if not raw:
        return set(DEFAULT_FORMS)
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def configured_lookback_days() -> int:
    raw = normalize_optional_text(os.getenv("SEC_LOOKBACK_DAYS"))
    value = int(raw) if raw else DEFAULT_LOOKBACK_DAYS
    if value < 1:
        raise ValueError("SEC_LOOKBACK_DAYS must be at least 1")
    return value


def collect_sec_filings(
    supabase: Client,
    session: requests.Session,
) -> list[CollectedArticle]:
    source = get_active_sec_source(supabase)
    if source is None:
        print("No active SEC API source was found in public.news_sources.")
        return []

    tickers = get_latest_company_tickers(supabase)
    if not tickers:
        print("No company tickers were found in the latest UFO holdings.")
        return []

    ticker_map = build_ticker_cik_map(download_json(session, SEC_TICKER_MAP_URL))
    ciks = sorted({ticker_map[ticker] for ticker in tickers if ticker in ticker_map})
    missing = sorted(tickers - ticker_map.keys())
    if missing:
        print(f"No SEC CIK mapping for {len(missing)} ticker(s): {', '.join(missing)}")

    cutoff = datetime.now(timezone.utc).date() - timedelta(
        days=configured_lookback_days()
    )
    forms = configured_forms()
    articles: list[CollectedArticle] = []
    for cik in ciks:
        try:
            # Keep this collector below the SEC's published 10 request/second cap.
            sleep(REQUEST_INTERVAL_SECONDS)
            payload = download_json(
                session, SEC_SUBMISSIONS_URL.format(cik=cik)
            )
            articles.extend(parse_recent_filings(payload, source, cutoff, forms))
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            print(f"SEC collection error for CIK {cik}: {type(exc).__name__}: {exc}")

    update_source_collection_time(supabase, source.source_id)
    print(
        f"Collected {len(articles)} recent SEC filing(s) for "
        f"{len(ciks)} mapped UFO holding(s)."
    )
    return articles


def main() -> None:
    supabase = create_supabase_client()
    session = create_sec_session()
    articles = collect_sec_filings(supabase, session)
    print_articles(articles, limit=10)
    save_articles(supabase, articles)


if __name__ == "__main__":
    main()
