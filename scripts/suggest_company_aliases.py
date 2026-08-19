from __future__ import annotations

import argparse
import json
from typing import Any

from agents.company_alias_agent import (
    research_company_aliases,
    save_alias_rows,
)
from collectors.company_sync import create_supabase_client
from processing.alias_validator import (
    database_rows_from_validation,
    validate_alias_suggestions,
)


def load_company(supabase: Any, company_id: int) -> dict[str, Any]:
    response = (
        supabase.table("companies")
        .select(
            "company_id,canonical_name,primary_ticker,"
            "exchange,country_code,website_url"
        )
        .eq("company_id", company_id)
        .limit(1)
        .execute()
    )

    if not response.data:
        raise RuntimeError(f"Company {company_id} was not found.")

    return response.data[0]


def load_existing_aliases(
    supabase: Any,
    company_id: int,
) -> list[dict[str, Any]]:
    response = (
        supabase.table("company_aliases")
        .select("alias,alias_type,verification_status,is_active")
        .eq("company_id", company_id)
        .execute()
    )
    return response.data or []


def load_all_aliases(supabase: Any) -> list[dict[str, Any]]:
    page_size = 1000
    start = 0
    aliases: list[dict[str, Any]] = []

    while True:
        response = (
            supabase.table("company_aliases")
            .select("company_id,alias,alias_type,verification_status,is_active")
            .range(start, start + page_size - 1)
            .execute()
        )
        page = response.data or []
        aliases.extend(page)

        if len(page) < page_size:
            return aliases

        start += page_size


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Research alias suggestions for one company."
    )
    parser.add_argument("--company-id", type=int, required=True)
    parser.add_argument(
        "--save",
        action="store_true",
        help=(
            "Save every alias that passes deterministic validation as active "
            "and VERIFIED."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    supabase = create_supabase_client()
    company = load_company(supabase, args.company_id)
    existing_aliases = load_existing_aliases(supabase, args.company_id)
    all_aliases = load_all_aliases(supabase)

    research = research_company_aliases(company, existing_aliases)
    validation_results = validate_alias_suggestions(
        company,
        research,
        existing_aliases,
        all_aliases,
    )
    rows = database_rows_from_validation(validation_results)

    print(
        json.dumps(
            {
                "company": company,
                "company_verified": research.company_verified,
                "company_summary": research.company_summary,
                "validation_results": [
                    result.as_dict() for result in validation_results
                ],
                "database_rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if not args.save:
        print("Preview only. Re-run with --save to insert verified aliases.")
        return

    inserted_count = save_alias_rows(supabase, rows)
    print(f"Inserted {inserted_count} validated alias rows.")


if __name__ == "__main__":
    main()
