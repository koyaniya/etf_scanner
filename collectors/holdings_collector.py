from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Any
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import Client, create_client

from processing.holdings_normalizer import classify_holding
from processing.company_resolver import (create_company, load_companies, resolve_candidate_company_id, resolve_company_id,)
from agents.company_enrichment_agent import ( build_company_candidate,
    classify_with_llm,
    normalize_company_candidate,
    should_accept_classification,
    validate_company_candidate,)
from collectors.company_sync import create_basic_aliases

ETF_TICKER = "UFO"
ETF_PAGE_URL = "https://procureetfs.com/ufo/"
TABLE_NAME = "etf_holdings"
LOG_TABLE_NAME = "etf_holding_update_logs"

MINIMUM_UPDATE_INTERVAL_DAYS = 3
REQUEST_TIMEOUT_SECONDS = 30
BATCH_SIZE = 200


DRY_RUN_COMPANY_CREATION = False


def create_supabase_client() -> Client:
    """Create an authenticated Supabase client."""

    load_dotenv()

    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is missing.")

    if not service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is missing.")

    return create_client(supabase_url, service_role_key)


def create_http_session() -> requests.Session:
    """Create an HTTP session with a descriptive user agent."""

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "UFOHoldingsResearchBot/1.0 "
                "(personal investment research project)"
            )
        }
    )
    return session


def url_exists(
    session: requests.Session,
    url: str,
) -> bool:
    """
    Check whether a holdings URL is downloadable.

    GET is used instead of HEAD because some WordPress servers
    do not handle HEAD requests consistently.
    """

    try:
        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            stream=True,
            allow_redirects=True,
        )

        content_type = response.headers.get("Content-Type", "").lower()

        is_valid = (
            response.status_code == 200
            and (
                "csv" in content_type
                or url.lower().endswith(".csv")
            )
        )

        response.close()
        return is_valid

    except requests.RequestException:
        return False


def generate_recent_holdings_urls(
    base_url: str,
    days_to_check: int = 14,
) -> list[str]:
    """
    Generate likely holdings filenames for recent dates.

    Example:
    UFO-JP-Holdings-Jul-28-2026.csv
    """

    parsed = urlparse(base_url)

    today = datetime.now(timezone.utc).date()
    candidates: list[str] = []

    for days_back in range(days_to_check):
        candidate_date = today - timedelta(days=days_back)

        # Holdings files generally correspond to trading days.
        if candidate_date.weekday() >= 5:
            continue

        month_folder = candidate_date.strftime("%Y/%m")
        filename = (
            f"UFO-JP-Holdings-"
            f"{candidate_date.strftime('%b-%d-%Y')}.csv"
        )

        candidate_url = (
            f"{parsed.scheme}://{parsed.netloc}"
            f"/wp-content/uploads/{month_folder}/{filename}"
        )

        candidates.append(candidate_url)

    return candidates


def find_latest_holdings_url(
    session: requests.Session,
) -> str:
    """
    Find the newest valid official UFO holdings CSV.

    First checks links published on the UFO page.
    If the page contains a stale link, checks recent filenames.
    """

    response = session.get(
        ETF_PAGE_URL,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    page_candidates: list[str] = []

    for link in soup.find_all("a", href=True):
        full_url = urljoin(
            ETF_PAGE_URL,
            link["href"].strip(),
        )

        normalized_url = full_url.lower()

        if (
            "ufo-jp-holdings" in normalized_url
            and normalized_url.endswith(".csv")
        ):
            page_candidates.append(full_url)

    # Remove duplicates while preserving order.
    page_candidates = list(dict.fromkeys(page_candidates))

    for candidate_url in page_candidates:
        print(f"Checking page URL: {candidate_url}")

        if url_exists(session, candidate_url):
            return candidate_url

        print("Page URL is unavailable; checking fallback dates.")

    # Use the discovered URL only as a pattern/base.
    fallback_base = (
        page_candidates[0]
        if page_candidates
        else ETF_PAGE_URL
    )

    fallback_candidates = generate_recent_holdings_urls(
        fallback_base,
        days_to_check=14,
    )

    for candidate_url in fallback_candidates:
        if candidate_url in page_candidates:
            continue

        print(f"Checking fallback URL: {candidate_url}")

        if url_exists(session, candidate_url):
            print(f"Found valid holdings file: {candidate_url}")
            return candidate_url

    raise RuntimeError(
        "No downloadable UFO holdings CSV was found for the "
        "last 14 calendar days. The official source may be "
        "temporarily unavailable."
    )


def download_holdings_csv(
    session: requests.Session,
    csv_url: str,
) -> pd.DataFrame:
    """Download and parse the official UFO holdings CSV."""

    response = session.get(
        csv_url,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    if response.status_code == 404:
        raise RuntimeError(
            "The official holdings link was found, but its file "
            f"does not exist: {csv_url}"
        )

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            f"The holdings file is empty: {csv_url}"
        )

    return pd.read_csv(
        BytesIO(response.content),
        encoding="utf-8-sig",
    )


def parse_percentage(value: Any) -> float | None:
    """Convert values such as '6.03%' into 6.03."""

    if pd.isna(value):
        return None

    cleaned = str(value).replace("%", "").replace(",", "").strip()

    if not cleaned:
        return None

    return float(cleaned)


def parse_number(value: Any) -> float | None:
    """Convert CSV numeric values into JSON-compatible floats."""

    if pd.isna(value):
        return None

    if isinstance(value, str):
        value = value.replace(",", "").strip()

        if not value:
            return None

    return float(value)


def parse_optional_text(value: Any) -> str | None:
    """Normalize optional text values."""

    if pd.isna(value):
        return None

    cleaned = str(value).strip()

    return cleaned or None


def clean_holdings(
    raw_df: pd.DataFrame,
    source_url: str,
) -> tuple[list[dict[str, Any]], date]:
    """Convert the official CSV into rows matching the Supabase table."""

    required_columns = {
        "Date",
        "Account",
        "StockTicker",
        "CUSIP",
        "SecurityName",
        "Shares",
        "Price",
        "MarketValue",
        "Weightings",
        "NetAssets",
        "SharesOutstanding",
        "CreationUnits",
    }

    missing_columns = required_columns - set(raw_df.columns)

    if missing_columns:
        raise ValueError(
            "Official CSV structure changed. Missing columns: "
            f"{sorted(missing_columns)}"
        )

    df = raw_df.copy()

    # The official CSV can contain a completely empty final row.
    df = df[df["SecurityName"].notna()].copy()

    # Confirm that the file belongs to UFO.
    df = df[df["Account"].astype(str).str.strip() == ETF_TICKER].copy()

    if df.empty:
        raise ValueError("The downloaded CSV contains no UFO holdings.")

    parsed_dates = pd.to_datetime(
        df["Date"],
        format="%m/%d/%Y",
        errors="coerce",
    )

    if parsed_dates.isna().any():
        raise ValueError("One or more holding dates could not be parsed.")

    unique_dates = parsed_dates.dt.date.unique()

    if len(unique_dates) != 1:
        raise ValueError(
            "Expected one snapshot date, but found: "
            f"{list(unique_dates)}"
        )

    holding_date = unique_dates[0]

    records: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        stock_ticker = parse_optional_text(row["StockTicker"])
        cusip = parse_optional_text(row["CUSIP"])
        security_name = parse_optional_text(row["SecurityName"])

        if not security_name:
            continue

        holding_type = classify_holding(
            stock_ticker,
            cusip,
            security_name,
        )

        records.append(
            {
                "etf_ticker": ETF_TICKER,
                "holding_date": holding_date.isoformat(),
                "stock_ticker": stock_ticker,
                "cusip": cusip,
                "security_name": security_name,
                "holding_type": holding_type,
                "shares": parse_number(row["Shares"]),
                "price": parse_number(row["Price"]),
                "market_value": parse_number(row["MarketValue"]),
                "weight": parse_percentage(row["Weightings"]),
                "net_assets": parse_number(row["NetAssets"]),
                "shares_outstanding": parse_number(
                    row["SharesOutstanding"]
                ),
                "creation_units": parse_number(row["CreationUnits"]),
                "source_url": source_url,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    validate_records(records)

    return records, holding_date


def validate_records(records: list[dict[str, Any]]) -> None:
    """Run basic checks before writing financial data to the database."""

    if not records:
        raise ValueError("No valid holding records were produced.")

    if len(records) < 20:
        raise ValueError(
            f"Only {len(records)} holdings were found. "
            "The source may be incomplete."
        )

    weights = [
        row["weight"]
        for row in records
        if row["weight"] is not None
    ]

    total_weight = sum(weights)

    # Allow a small difference due to source rounding.
    if not 98 <= total_weight <= 102:
        raise ValueError(
            f"Unexpected total portfolio weight: {total_weight:.2f}%"
        )

    unique_keys = {
        (
            row["etf_ticker"],
            row["holding_date"],
            row["cusip"],
            row["security_name"],
        )
        for row in records
    }

    if len(unique_keys) != len(records):
        raise ValueError("Duplicate holdings exist in the downloaded file.")


def get_latest_saved_date(
    supabase: Client,
) -> date | None:
    """Return the newest UFO snapshot date currently saved."""

    response = (
        supabase.table(TABLE_NAME)
        .select("holding_date")
        .eq("etf_ticker", ETF_TICKER)
        .order("holding_date", desc=True)
        .limit(1)
        .execute()
    )

    if not response.data:
        return None

    return date.fromisoformat(response.data[0]["holding_date"])


def should_run_update(
    latest_saved_date: date | None,
    today: date,
) -> bool:
    """Check whether at least three days have passed."""

    if latest_saved_date is None:
        return True

    next_allowed_date = latest_saved_date + timedelta(
        days=MINIMUM_UPDATE_INTERVAL_DAYS
    )

    return today >= next_allowed_date


def upsert_in_batches(
    supabase: Client,
    records: list[dict[str, Any]],
) -> int:
    """Bulk-upsert the holdings into Supabase."""

    rows_saved = 0

    for start in range(0, len(records), BATCH_SIZE):
        batch = records[start : start + BATCH_SIZE]

        response = (
            supabase.table(TABLE_NAME)
            .upsert(
                batch,
                on_conflict=(
                    "etf_ticker,"
                    "holding_date,"
                    "cusip,"
                    "security_name"
                ),
            )
            .execute()
        )

        rows_saved += len(response.data or batch)

    return rows_saved


def write_log(
    supabase: Client,
    *,
    status: str,
    message: str,
    source_url: str | None = None,
    holding_date: date | None = None,
    rows_received: int | None = None,
    rows_saved: int | None = None,
) -> None:
    """Save an execution result to the update-log table."""

    log_record = {
        "etf_ticker": ETF_TICKER,
        "source_url": source_url,
        "holding_date": (
            holding_date.isoformat() if holding_date else None
        ),
        "rows_received": rows_received,
        "rows_saved": rows_saved,
        "status": status,
        "message": message,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    supabase.table(LOG_TABLE_NAME).insert(log_record).execute()


def main() -> None:
    supabase = create_supabase_client()
    session = create_http_session()

    source_url: str | None = None
    holding_date: date | None = None

    try:
        latest_saved_date = get_latest_saved_date(supabase)
        today = datetime.now(timezone.utc).date()

        if not should_run_update(latest_saved_date, today):
            next_update_date = latest_saved_date + timedelta(
                days=MINIMUM_UPDATE_INTERVAL_DAYS
            )

            message = (
                f"Skipped. Latest saved snapshot is {latest_saved_date}. "
                f"Next update is allowed on {next_update_date}."
            )

            print(message)

            write_log(
                supabase,
                status="SKIPPED",
                message=message,
                holding_date=latest_saved_date,
            )
            return

        source_url = find_latest_holdings_url(session)
        print(f"Official holdings URL: {source_url}")

        raw_df = download_holdings_csv(session, source_url)

        records, holding_date = clean_holdings(
            raw_df=raw_df,
            source_url=source_url,
        )

        companies = load_companies(supabase)

        for record in records:
            record["company_id"] = resolve_company_id(
                holding=record,
                companies=companies,
            )


        for record in records:
            print( record["stock_ticker"],
                record["security_name"],
                record["holding_type"],
                record["company_id"],
            )
            



        

        unresolved_companies = [
            record 
            for record in records 
            if record["holding_type"] == "COMPANY" 
            and record["company_id"] is None
            ]


        for record in unresolved_companies: 
            llm_result = classify_with_llm(record)
        
            print(
                "LLM classification:",
                record["stock_ticker"],
                record["security_name"],
                llm_result,
            )

            if not should_accept_classification(llm_result):
                continue

            # LLM verified that this is not actually a company.
            if llm_result["entity_type"] != "COMPANY":
                record["holding_type"] = llm_result["entity_type"]
                continue


            # Verified company: build enriched candidate.
            candidate = build_company_candidate(
                holding=record,
                llm_result=llm_result,
            )

            candidate = normalize_company_candidate(candidate)

            validate_company_candidate(candidate)


            # Check again against existing master using enriched data.
            existing_company_id = resolve_candidate_company_id(
                candidate=candidate,
                companies=companies,
                )


            if existing_company_id is not None:
                record["company_id"] = existing_company_id
                continue

            # Truly new, verified company.
            if DRY_RUN_COMPANY_CREATION:
                print(
                    "DRY RUN - would create company:",
                    candidate,
                )
                continue

            new_company_id = create_company(
                supabase=supabase,
                candidate=candidate,
            )

            record["company_id"] = new_company_id

            companies.append(
                {
                    "company_id": new_company_id,
                    "canonical_name": candidate["canonical_name"],
                    "primary_ticker": candidate.get("stock_ticker"),
                }
            )

            print(
                "Created new company:",
                new_company_id,
                candidate["canonical_name"],
            )


        still_unresolved_companies = [
            record
            for record in records 
            if record["holding_type"] == "COMPANY"
            and record["company_id"] is None]

        if still_unresolved_companies: 
            print("Still unresolved after LLM classification:")
            for record in still_unresolved_companies:
                print(
                record["stock_ticker"],
                record["cusip"],
                record["security_name"],
                )


        # Keep free, deterministic aliases current independently of the
        # separately scheduled AI alias-enrichment workflow.
        create_basic_aliases(supabase, companies)


        rows_saved = upsert_in_batches(supabase, records)

        message = (
            f"Saved {rows_saved} UFO holdings "
            f"for snapshot date {holding_date}."
        )

        print(message)

        write_log(
            supabase,
            status="SUCCESS",
            message=message,
            source_url=source_url,
            holding_date=holding_date,
            rows_received=len(records),
            rows_saved=rows_saved,
        )

    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        print(error_message, file=sys.stderr)

        try:
            write_log(
                supabase,
                status="FAILED",
                message=error_message,
                source_url=source_url,
                holding_date=holding_date,
            )
        except Exception as log_exc:
            print(
                f"Could not write failure log: {log_exc}",
                file=sys.stderr,
            )

        raise


if __name__ == "__main__":
    main()
