from __future__ import annotations

import unittest
from datetime import datetime, timezone

from collectors.holdings_collector import write_log


class FakeInsert:
    def execute(self):
        return None


class FakeTable:
    def __init__(self) -> None:
        self.record = None

    def insert(self, record):
        self.record = record
        return FakeInsert()


class FakeSupabase:
    def __init__(self) -> None:
        self.table_client = FakeTable()

    def table(self, name):
        self.table_name = name
        return self.table_client


class HoldingsLogTests(unittest.TestCase):
    def test_log_keeps_actual_start_and_korea_offset(self) -> None:
        supabase = FakeSupabase()
        started_at = datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc)

        write_log(
            supabase,
            started_at=started_at,
            status="SUCCESS",
            message="Done.",
        )

        record = supabase.table_client.record
        self.assertEqual(
            record["started_at"],
            "2026-08-12T10:00:00+09:00",
        )
        self.assertTrue(record["completed_at"].endswith("+09:00"))


if __name__ == "__main__":
    unittest.main()
