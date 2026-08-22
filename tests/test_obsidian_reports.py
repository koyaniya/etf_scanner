from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from agents.daily_summary_agent import DailySummary
from processing.obsidian_reports import (
    build_event_documents,
    generate_obsidian_reports,
    report_window,
    select_daily_events,
)


def sample_data() -> dict:
    return {
        "events": [
            {
                "event_id": 10,
                "status": "ACTIVE",
                "canonical_title": "Launch succeeds",
                "event_type": "LAUNCH_SUCCESS",
                "impact_direction": "POSITIVE",
                "impact_strength": 4,
                "importance_score": 80,
                "confidence": 0.9,
                "time_horizon": "SHORT_TERM",
                "event_date": "2026-08-21",
                "canonical_summary": "A launch succeeded.",
                "industry_implication": "Capacity increased.",
                "facts": ["The payload reached orbit."],
                "risk_factors": [],
                "merged_into_event_id": None,
                "updated_at": "2026-08-21T01:00:00+00:00",
            },
            {
                "event_id": 11,
                "status": "MERGED",
                "canonical_title": "Duplicate launch",
                "merged_into_event_id": 10,
            },
        ],
        "event_articles": [
            {"event_id": 10, "analysis_id": 100},
            {"event_id": 10, "analysis_id": 101},
        ],
        "analyses": [
            {"analysis_id": 100, "article_id": 1000},
            {"analysis_id": 101, "article_id": 1001},
        ],
        "articles": [
            {
                "article_id": 1000,
                "title": "First report",
                "url": "https://example.com/1",
                "published_at": "2026-08-20T15:30:00Z",
            },
            {
                "article_id": 1001,
                "title": "Follow-up",
                "url": "https://example.com/2",
                "published_at": "2026-08-21T02:00:00Z",
            },
        ],
        "event_companies": [
            {"event_id": 10, "company_id": 5, "company_role": "AFFECTED"}
        ],
        "companies": [
            {"company_id": 5, "canonical_name": "Example Space", "primary_ticker": "EX"}
        ],
        "event_topics": [{"event_id": 10, "topic_id": 7}],
        "topics": [{"topic_id": 7, "topic_name": "Launch services"}],
    }


class ObsidianReportTests(unittest.TestCase):
    def test_daily_events_use_rolling_korea_window_and_deduplicate(self) -> None:
        events = select_daily_events(sample_data(), date(2026, 8, 21))
        self.assertEqual([event["event_id"] for event in events], [10])

    def test_report_window_ends_at_0830_korea_time(self) -> None:
        start, end = report_window(date(2026, 8, 21))
        self.assertEqual(start.isoformat(), "2026-08-20T08:30:00+09:00")
        self.assertEqual(end.isoformat(), "2026-08-21T08:30:00+09:00")

    def test_window_start_is_inclusive_and_end_is_exclusive(self) -> None:
        data = sample_data()
        data["articles"][0]["published_at"] = "2026-08-19T23:30:00Z"
        data["articles"][1]["published_at"] = "2026-08-20T23:30:00Z"
        events = select_daily_events(data, date(2026, 8, 21))
        self.assertEqual([event["event_id"] for event in events], [10])

        data["articles"][0]["published_at"] = "2026-08-19T23:29:59Z"
        events = select_daily_events(data, date(2026, 8, 21))
        self.assertEqual(events, [])

    def test_event_note_contains_sources_and_merged_note_redirects(self) -> None:
        documents = build_event_documents(sample_data())
        self.assertIn("[First report](https://example.com/1)", documents[10])
        self.assertIn("2026-08-21 00:30 KST", documents[10])
        self.assertIn(
            'companies:\n  - "Example Space [company_id: 5]"', documents[10]
        )
        self.assertIn("[[event-10|Launch succeeds]]", documents[11])
        self.assertNotIn("A launch succeeded", documents[11])

    def test_empty_day_writes_summary_without_calling_ai(self) -> None:
        data = sample_data()
        summarizer = Mock()
        with tempfile.TemporaryDirectory() as directory, patch(
            "processing.obsidian_reports.load_vault_data", return_value=data
        ):
            result = generate_obsidian_reports(
                object(),
                summary_date=date(2026, 8, 23),
                vault_dir=Path(directory),
                summarizer=summarizer,
            )
            content = Path(result["summary_path"]).read_text(encoding="utf-8")
        summarizer.assert_not_called()
        self.assertIn("No canonical events", content)

    def test_generation_writes_linked_daily_summary(self) -> None:
        summary = DailySummary(
            overview="Launch activity was positive.",
            themes=["Launch capacity"],
            risks=["Execution remains a risk."],
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "processing.obsidian_reports.load_vault_data",
            return_value=sample_data(),
        ):
            result = generate_obsidian_reports(
                object(),
                summary_date=date(2026, 8, 21),
                vault_dir=Path(directory),
                model="test-model",
                summarizer=lambda *args, **kwargs: summary,
            )
            content = Path(result["summary_path"]).read_text(encoding="utf-8")
        self.assertIn("[[event-10|Launch succeeds]]", content)
        self.assertIn("Example Space [company_id: 5]", content)
        self.assertIn("Launch activity was positive.", content)
        self.assertIn('window_start: "2026-08-20T08:30:00+09:00"', content)
        self.assertIn('window_end: "2026-08-21T08:30:00+09:00"', content)
        self.assertEqual(result["daily_events"], 1)


if __name__ == "__main__":
    unittest.main()
