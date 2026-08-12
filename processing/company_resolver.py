from __future__ import annotations

from typing import Any

from supabase import Client


def normalize_text(value: str | None) -> str:
    if not value:
        return ""

    return value.strip().upper()

def load_companies(
    supabase: Client,
) -> list[dict]:
    """
    Load company master data needed for holding resolution.
    """

    response = (
        supabase.table("companies")
        .select(
            "company_id,"
            "canonical_name,"
            "primary_ticker"
        )
        .execute()
    )

    return response.data or []


def resolve_company_id(
    holding: dict[str, Any],
    companies: list[dict[str, Any]],
) -> int | None:
    """
    Match a holding to an existing company.

    Matching order:
    1. stock_ticker -> primary_ticker
    2. security_name -> canonical_name

    Returns company_id if matched, otherwise None.
    """

    if holding.get("holding_type") != "COMPANY":
        return None

    stock_ticker = normalize_text(holding.get("stock_ticker"))
    security_name = normalize_text(holding.get("security_name"))

    # 1. Match by ticker
    if stock_ticker:
        for company in companies:
            primary_ticker = normalize_text(company.get("primary_ticker"))

            if stock_ticker == primary_ticker:
                return company["company_id"]

    # 2. Match by company name
    if security_name:
        for company in companies:
            canonical_name = normalize_text(company.get("canonical_name"))

            if security_name == canonical_name:
                return company["company_id"]

    return None


def create_company(
    supabase: Client,
    candidate: dict[str, Any],
) -> int:
    """
    Insert a verified new company into the company master
    and return its generated company_id.
    """

    canonical_name = candidate.get("canonical_name")
    primary_ticker = candidate.get("stock_ticker")

    if not canonical_name:
        raise ValueError(
            "Cannot create company without canonical_name."
        )

    response = (
        supabase.table("companies")
        .insert(
            {
                "canonical_name": canonical_name,
                "primary_ticker": primary_ticker,
                "exchange": candidate.get("exchange"),
                "country_code": candidate.get("country_code"),
                "website_url": candidate.get("website_url"),
                "is_active": True,
            }
        )
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Company insert succeeded but returned no data."
        )

    return response.data[0]["company_id"]


def resolve_candidate_company_id(
    candidate: dict[str, Any],
    companies: list[dict[str, Any]],
) -> int | None:
    """
    Try to match an enriched company candidate
    against the existing company master.
    """

    stock_ticker = normalize_text(
        candidate.get("stock_ticker")
    )
    canonical_name = normalize_text(
        candidate.get("canonical_name")
    )
    security_name = normalize_text(
        candidate.get("security_name")
    )

    # 1. Match by ticker
    if stock_ticker:
        for company in companies:
            primary_ticker = normalize_text(
                company.get("primary_ticker")
            )

            if stock_ticker == primary_ticker:
                return company["company_id"]

    # 2. Match by enriched canonical name
    if canonical_name:
        for company in companies:
            existing_name = normalize_text(
                company.get("canonical_name")
            )

            if canonical_name == existing_name:
                return company["company_id"]

    # 3. Match by source security name
    if security_name:
        for company in companies:
            existing_name = normalize_text(
                company.get("canonical_name")
            )

            if security_name == existing_name:
                return company["company_id"]

    return None