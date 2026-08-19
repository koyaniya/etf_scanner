from __future__ import annotations

import argparse
import json

from processing.event_candidates import find_event_candidates
from processing.relevance_filter import create_supabase_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview canonical event candidates for one article analysis."
    )
    parser.add_argument("--analysis-id", type=int, required=True)
    parser.add_argument("--window-days", type=int)
    parser.add_argument("--limit", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.limit <= 100:
        raise ValueError("--limit must be between 1 and 100.")
    if args.window_days is not None and args.window_days < 1:
        raise ValueError("--window-days must be at least 1.")
    analysis, candidates = find_event_candidates(
        create_supabase_client(),
        args.analysis_id,
        window_days=args.window_days,
        limit=args.limit,
    )
    print(json.dumps({
        "mode": "PREVIEW",
        "analysis_id": analysis["analysis_id"],
        "article_id": analysis["article_id"],
        "event_type": analysis["event_type"],
        "candidate_count": len(candidates),
        "candidates": [candidate.as_dict() for candidate in candidates],
    }, ensure_ascii=False, indent=2))
    print("Preview only. No clustering decision or event was saved.")


if __name__ == "__main__":
    main()
