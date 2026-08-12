from __future__ import annotations

import argparse
import json

from collectors.company_sync import create_supabase_client
from processing.alias_enrichment import run_alias_enrichment_batch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Research and validate aliases for a bounded company batch."
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--company-id", type=int)
    parser.add_argument("--model")
    parser.add_argument(
        "--refresh-days",
        type=int,
        help=(
            "Recheck successful companies after this many days; defaults to "
            "ALIAS_RESEARCH_REFRESH_DAYS or 180."
        ),
    )
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--retry-delay-seconds", type=float, default=2)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Include companies with a previous successful research run.",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save aliases and persistent run results; default is preview only.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.limit <= 100:
        raise ValueError("--limit must be between 1 and 100.")
    if not 1 <= args.max_attempts <= 5:
        raise ValueError("--max-attempts must be between 1 and 5.")
    if args.retry_delay_seconds < 0:
        raise ValueError("--retry-delay-seconds must not be negative.")

    result = run_alias_enrichment_batch(
        create_supabase_client(),
        limit=args.limit,
        save=args.save,
        force=args.force,
        company_id=args.company_id,
        model=args.model,
        refresh_days=args.refresh_days,
        max_attempts=args.max_attempts,
        retry_delay_seconds=args.retry_delay_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
