from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client


HOLDINGS_TABLE = "etf_holdings"
COMPANIES_TABLE = "companies"
ALIASES_TABLE = "company_aliases"
ETF_TICKER = "UFO"


def create_supabase_client() -> Client:
    load_dotenv(override=True)

    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

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


def get_latest_holding_date(
    supabase: Client,
) -> str:
    response = (
        supabase.table(HOLDINGS_TABLE)
        .select("holding_date")
        .eq("etf_ticker", ETF_TICKER)
        .order("holding_date", desc=True)
        .limit(1)
        .execute()
    )

    if not response.data:
        raise RuntimeError("No UFO holdings were found.")

    return response.data[0]["holding_date"]


def get_latest_holdings(
    supabase: Client,
    holding_date: str,
) -> list[dict[str, Any]]:
    response = (
        supabase.table(HOLDINGS_TABLE)
        .select(
            "holding_id,"
            "security_name,"
            "stock_ticker,"
            "company_id"
        )
        .eq("etf_ticker", ETF_TICKER)
        .eq("holding_date", holding_date)
        .execute()
    )

    return response.data or []


def get_existing_companies(
    supabase: Client,
) -> list[dict[str, Any]]:
    response = (
        supabase.table(COMPANIES_TABLE)
        .select(
            "company_id,"
            "canonical_name,"
            "primary_ticker"
        )
        .execute()
    )

    return response.data or []


def normalize_ticker(
    ticker: str | None,
) -> str | None:
    if ticker is None:
        return None

    normalized = ticker.strip().upper()

    return normalized or None


def normalize_name(name: str) -> str:
    return " ".join(name.strip().split())


def create_missing_companies(
    supabase: Client,
    holdings: list[dict[str, Any]],
    existing_companies: list[dict[str, Any]],
) -> None:
    existing_tickers = {
        normalize_ticker(company["primary_ticker"])
        for company in existing_companies
        if company.get("primary_ticker")
    }

    existing_names = {
        normalize_name(company["canonical_name"]).lower()
        for company in existing_companies
    }

    new_companies: list[dict[str, Any]] = []

    for holding in holdings:
        ticker = normalize_ticker(
            holding.get("stock_ticker")
        )
        company_name = normalize_name(
            holding["security_name"]
        )

        if not ticker:
            continue

        if ticker in existing_tickers:
            continue

        if company_name.lower() in existing_names:
            continue

        new_companies.append(
            {
                "canonical_name": company_name,
                "primary_ticker": ticker,
            }
        )

        existing_tickers.add(ticker)
        existing_names.add(company_name.lower())

    if new_companies:
        (
            supabase.table(COMPANIES_TABLE)
            .insert(new_companies)
            .execute()
        )

        print(
            f"Created {len(new_companies)} companies."
        )
    else:
        print("No new companies were required.")


def create_basic_aliases(
    supabase: Client,
    companies: list[dict[str, Any]],
) -> None:
    aliases: list[dict[str, Any]] = []

    for company in companies:
        company_id = company["company_id"]
        canonical_name = company["canonical_name"]
        ticker = normalize_ticker(
            company.get("primary_ticker")
        )

        aliases.append(
            {
                "company_id": company_id,
                "alias": canonical_name,
                "alias_type": "OFFICIAL_NAME",
            }
        )

        if ticker:
            aliases.append(
                {
                    "company_id": company_id,
                    "alias": ticker,
                    "alias_type": "TICKER",
                }
            )

    if aliases:
        (
            supabase.table(ALIASES_TABLE)
            .upsert(
                aliases,
                on_conflict="company_id,alias",
            )
            .execute()
        )

    print(f"Upserted {len(aliases)} basic aliases.")


def connect_holdings_to_companies(
    supabase: Client,
    holdings: list[dict[str, Any]],
    companies: list[dict[str, Any]],
) -> None:
    companies_by_ticker = {
        normalize_ticker(company["primary_ticker"]):
            company["company_id"]
        for company in companies
        if company.get("primary_ticker")
    }

    updated_count = 0

    for holding in holdings:
        if holding.get("company_id"):
            continue

        ticker = normalize_ticker(
            holding.get("stock_ticker")
        )

        company_id = companies_by_ticker.get(ticker)

        if not company_id:
            continue

        (
            supabase.table(HOLDINGS_TABLE)
            .update({"company_id": company_id})
            .eq("holding_id", holding["holding_id"])
            .execute()
        )

        updated_count += 1

    print(
        f"Connected {updated_count} holdings "
        "to companies."
    )


def main() -> None:
    supabase = create_supabase_client()

    holding_date = get_latest_holding_date(supabase)
    holdings = get_latest_holdings(
        supabase,
        holding_date,
    )

    existing_companies = get_existing_companies(
        supabase
    )

    create_missing_companies(
        supabase,
        holdings,
        existing_companies,
    )

    # Reload companies because new rows may have been added.
    companies = get_existing_companies(supabase)

    create_basic_aliases(
        supabase,
        companies,
    )

    connect_holdings_to_companies(
        supabase,
        holdings,
        companies,
    )


if __name__ == "__main__":
    main()