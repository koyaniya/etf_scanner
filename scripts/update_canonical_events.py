from __future__ import annotations

import argparse
import json

from processing.event_updates import run_event_update_batch
from processing.relevance_filter import create_supabase_client


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consolidate multi-article canonical events with an audit trail."
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--event-id", type=int)
    parser.add_argument("--model")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        raise ValueError("--limit must be between 1 and 100.")
    result = run_event_update_batch(
        create_supabase_client(), limit=args.limit, save=args.save,
        event_id=args.event_id, model=args.model,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_error and result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
