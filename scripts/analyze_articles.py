from __future__ import annotations

import argparse
import json

from processing.article_analysis import run_article_analysis_batch
from processing.relevance_filter import create_supabase_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze a bounded batch of eligible articles."
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--article-id", type=int)
    parser.add_argument("--model")
    parser.add_argument(
        "--save",
        action="store_true",
        help="Persist analysis runs and resolved entity relationships.",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help=(
            "Exit unsuccessfully after completing the batch if any article failed."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.limit <= 100:
        raise ValueError("--limit must be between 1 and 100.")
    result = run_article_analysis_batch(
        create_supabase_client(),
        limit=args.limit,
        save=args.save,
        article_id=args.article_id,
        model=args.model,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_error and result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
