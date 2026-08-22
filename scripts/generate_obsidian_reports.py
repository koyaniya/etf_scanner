from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from processing.obsidian_reports import generate_obsidian_reports
from processing.relevance_filter import create_supabase_client


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export canonical events and generate an Obsidian daily summary."
    )
    parser.add_argument(
        "--date",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
        default=datetime.now(ZoneInfo("Asia/Seoul")).date(),
        help=(
            "Korea-local report date (YYYY-MM-DD). The report covers the rolling "
            "24 hours ending at 08:30 KST on this date."
        ),
    )
    parser.add_argument("--vault-dir", type=Path, default=Path("daily_summaries"))
    parser.add_argument("--model")
    args = parser.parse_args()
    result = generate_obsidian_reports(
        create_supabase_client(),
        summary_date=args.date,
        vault_dir=args.vault_dir,
        model=args.model,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
