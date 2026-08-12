from __future__ import annotations

import os
import re
import unicodedata
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client


HOLDINGS_TABLE = "etf_holdings"
COMPANIES_TABLE = "companies"
ALIASES_TABLE = "company_aliases"
ETF_TICKER = "UFO"

LEGAL_SUFFIX_PATTERN = re.compile(
    r"(?:,?\s+)(?:"
    r"inc(?:orporated)?|corp(?:oration)?|company|co|"
    r"limited|ltd|llc|plc|pbc|"
    r"s\.?e\.?|a\.?b\.?|"
    r"s\.?a\.?c\.?a\.?|s\.?p\.?a\.?|"
    r"s\.?a\.?|n\.?v\.?)\.?$",
    flags=re.IGNORECASE,
)

# These are vendor/source naming artifacts, not corporate legal forms. Keeping
# them separate prevents the legal-suffix rules from becoming data-source specific.
SOURCE_NAME_ARTIFACT_PATTERN = re.compile(
    r"(?:\s*/\s*(?:korea|japan)|(?:,?\s+)co/the)$",
    flags=re.IGNORECASE,
)

GENERIC_SHORT_NAMES = {
    "aerospace",
    "communications",
    "company",
    "corporation",
    "global",
    "holdings",
    "industries",
    "international",
    "space",
    "technologies",
    "technology",
}


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

    normalized = normalize_name(ticker).upper()

    return normalized or None


def normalize_name(name: str | None) -> str:
    if not name:
        return ""

    normalized = unicodedata.normalize("NFKC", name)
    return " ".join(normalized.strip().split())


def normalize_alias_key(alias: str | None) -> str:
    """Return a stable case- and whitespace-insensitive alias key."""

    return normalize_name(alias).casefold()


def is_safe_ticker_alias(ticker: str | None) -> bool:
    """Reject ticker values likely to cause broad or malformed text matches."""

    if not ticker or not 2 <= len(ticker) <= 15:
        return False

    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]*", ticker))


def derive_short_name(canonical_name: str | None) -> str | None:
    """Remove recognized trailing legal suffixes from a company name."""

    short_name = normalize_name(canonical_name)
    original_key = normalize_alias_key(short_name)

    while short_name:
        stripped = SOURCE_NAME_ARTIFACT_PATTERN.sub("", short_name)
        stripped = LEGAL_SUFFIX_PATTERN.sub("", stripped).strip(" ,")
        if stripped == short_name:
            break
        short_name = normalize_name(stripped)

    short_key = normalize_alias_key(short_name)
    alphanumeric_length = len(re.sub(r"[^\w]", "", short_name))

    if (
        not short_name
        or short_key == original_key
        or alphanumeric_length < 4
        or short_key in GENERIC_SHORT_NAMES
    ):
        return None

    return short_name


def build_deterministic_aliases(
    companies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build safe, verified aliases without external research or an LLM."""

    aliases: list[dict[str, Any]] = []
    candidate_keys: set[tuple[int, str]] = set()

    def add_alias(
        company_id: int,
        alias: str | None,
        alias_type: str,
        confidence: float,
    ) -> None:
        normalized_alias = normalize_name(alias)
        key = (company_id, normalize_alias_key(normalized_alias))

        if not normalized_alias or not key[1] or key in candidate_keys:
            return

        candidate_keys.add(key)
        aliases.append(
            {
                "company_id": company_id,
                "alias": normalized_alias,
                "alias_type": alias_type,
                "is_active": True,
                "confidence": confidence,
                "verification_status": "VERIFIED",
                "source_urls": [],
                "generated_by": "DETERMINISTIC",
            }
        )

    for company in companies:
        company_id = company["company_id"]
        canonical_name = normalize_name(company.get("canonical_name"))
        ticker = normalize_ticker(company.get("primary_ticker"))

        add_alias(company_id, canonical_name, "OFFICIAL_NAME", 1.0)

        if is_safe_ticker_alias(ticker):
            add_alias(company_id, ticker, "TICKER", 1.0)

        add_alias(
            company_id,
            derive_short_name(canonical_name),
            "SHORT_NAME",
            0.95,
        )

    return aliases


def filter_missing_aliases(
    candidates: list[dict[str, Any]],
    existing_aliases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Exclude candidates already stored for the same company."""

    existing_keys = {
        (
            row["company_id"],
            normalize_alias_key(row.get("alias")),
        )
        for row in existing_aliases
        if row.get("alias")
    }

    return [
        alias
        for alias in candidates
        if (
            alias["company_id"],
            normalize_alias_key(alias["alias"]),
        )
        not in existing_keys
    ]


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
) -> int:
    candidates = build_deterministic_aliases(companies)

    existing_response = (
        supabase.table(ALIASES_TABLE)
        .select("company_id,alias")
        .execute()
    )
    new_aliases = filter_missing_aliases(
        candidates,
        existing_response.data or [],
    )

    if new_aliases:
        (
            supabase.table(ALIASES_TABLE)
            .insert(new_aliases)
            .execute()
        )

    print(
        f"Created {len(new_aliases)} deterministic aliases "
        f"({len(candidates) - len(new_aliases)} already existed)."
    )
    return len(new_aliases)


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
