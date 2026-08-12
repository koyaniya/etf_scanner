from __future__ import annotations

import argparse
import json

from collectors.company_sync import create_supabase_client
from processing.alias_review import (
    apply_review_action,
    list_aliases_for_review,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review AI-generated company aliases."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List aliases by status.")
    list_parser.add_argument(
        "--status",
        choices=["PENDING", "VERIFIED", "REJECTED"],
        default="PENDING",
    )
    list_parser.add_argument("--company-id", type=int)
    list_parser.add_argument("--limit", type=int, default=100)

    decide_parser = subparsers.add_parser(
        "decide",
        help="Apply one review decision to an alias.",
    )
    decide_parser.add_argument("--alias-id", type=int, required=True)
    decide_parser.add_argument(
        "--action",
        choices=["approve", "reject", "deactivate", "reopen"],
        required=True,
    )
    decide_parser.add_argument("--reviewed-by", required=True)
    decide_parser.add_argument("--note")

    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.command == "list" and not 1 <= args.limit <= 1000:
        raise ValueError("--limit must be between 1 and 1000.")


def main() -> None:
    args = parse_args()
    validate_args(args)
    supabase = create_supabase_client()

    if args.command == "list":
        rows = list_aliases_for_review(
            supabase,
            status=args.status,
            company_id=args.company_id,
            limit=args.limit,
        )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        print(f"Found {len(rows)} aliases.")
        return

    row = apply_review_action(
        supabase,
        args.alias_id,
        args.action,
        args.reviewed_by,
        note=args.note,
    )
    print(json.dumps(row, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
