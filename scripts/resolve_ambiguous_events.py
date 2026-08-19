from __future__ import annotations

import argparse
import json

from processing.event_llm_clustering import run_llm_event_clustering_batch
from processing.relevance_filter import create_supabase_client


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve ambiguous event clusters with structured LLM comparison."
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--analysis-id", type=int)
    parser.add_argument("--window-days", type=int)
    parser.add_argument("--model")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        raise ValueError("--limit must be between 1 and 100.")
    result = run_llm_event_clustering_batch(
        create_supabase_client(), limit=args.limit, save=args.save,
        analysis_id=args.analysis_id, window_days=args.window_days,
        model=args.model,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_error and result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
