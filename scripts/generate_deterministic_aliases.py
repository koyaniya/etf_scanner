from __future__ import annotations

import argparse
import json

from collectors.company_sync import (
    ALIASES_TABLE,
    build_deterministic_aliases,
    create_basic_aliases,
    create_supabase_client,
    filter_missing_aliases,
    get_existing_companies,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate safe company aliases without OpenAI."
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Insert missing deterministic aliases; default is preview only.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    supabase = create_supabase_client()
    companies = get_existing_companies(supabase)

    if args.save:
        created = create_basic_aliases(supabase, companies)
        print(f"Saved {created} deterministic aliases.")
        return

    existing_response = (
        supabase.table(ALIASES_TABLE)
        .select("company_id,alias")
        .execute()
    )
    missing = filter_missing_aliases(
        build_deterministic_aliases(companies),
        existing_response.data or [],
    )
    print(json.dumps(missing, ensure_ascii=False, indent=2))
    print(
        f"Preview: {len(missing)} deterministic aliases would be saved."
    )


if __name__ == "__main__":
    main()
