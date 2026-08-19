from __future__ import annotations

import argparse
import json

from processing.event_clustering import run_event_clustering_batch
from processing.relevance_filter import create_supabase_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview or save deterministic canonical-event clustering."
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--analysis-id", type=int)
    parser.add_argument("--window-days", type=int)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.limit <= 100:
        raise ValueError("--limit must be between 1 and 100.")
    if args.window_days is not None and args.window_days < 1:
        raise ValueError("--window-days must be at least 1.")
    result = run_event_clustering_batch(
        create_supabase_client(),
        limit=args.limit,
        save=args.save,
        analysis_id=args.analysis_id,
        window_days=args.window_days,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_error and result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
