from __future__ import annotations

import unittest

from processing.event_candidates import rank_event_candidates


class EventCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analysis = {
            "has_event": True,
            "event_type": "GOVERNMENT_CONTRACT",
            "event_date": "2026-08-18",
            "published_at": "2026-08-19T08:00:00+00:00",
            "company_ids": [60],
            "topic_ids": [7, 9, 10],
        }

    def test_matching_company_topics_and_date_rank_first(self) -> None:
        events = [
            {
                "event_id": 2,
                "status": "ACTIVE",
                "canonical_title": "Different government award",
                "event_type": "GOVERNMENT_CONTRACT",
                "event_date": "2026-08-17",
                "company_ids": [99],
                "topic_ids": [9],
            },
            {
                "event_id": 1,
                "status": "ACTIVE",
                "canonical_title": "NRO expands HawkEye 360 access",
                "event_type": "GOVERNMENT_CONTRACT",
                "event_date": "2026-08-18",
                "company_ids": [60],
                "topic_ids": [7, 9, 10],
            },
        ]
        candidates = rank_event_candidates(
            self.analysis, events, window_days=30
        )
        self.assertEqual([row.event_id for row in candidates], [1, 2])
        self.assertEqual(candidates[0].similarity_score, 1.0)

    def test_wrong_type_inactive_and_outside_window_are_excluded(self) -> None:
        events = [
            {
                "event_id": 1, "status": "ACTIVE",
                "event_type": "PARTNERSHIP", "event_date": "2026-08-18",
            },
            {
                "event_id": 2, "status": "MERGED",
                "event_type": "GOVERNMENT_CONTRACT", "event_date": "2026-08-18",
            },
            {
                "event_id": 3, "status": "ACTIVE",
                "event_type": "GOVERNMENT_CONTRACT", "event_date": "2026-06-01",
            },
        ]
        self.assertEqual(
            rank_event_candidates(self.analysis, events, window_days=30), []
        )

    def test_publication_dates_are_fallback_when_event_dates_are_missing(self) -> None:
        analysis = {**self.analysis, "event_date": None}
        events = [{
            "event_id": 1,
            "status": "ACTIVE",
            "canonical_title": "Related contract",
            "event_type": "GOVERNMENT_CONTRACT",
            "event_date": None,
            "last_published_at": "2026-08-17T12:00:00+00:00",
            "company_ids": [60],
            "topic_ids": [7],
        }]
        result = rank_event_candidates(analysis, events, window_days=30)
        self.assertEqual(result[0].days_apart, 2)

    def test_no_event_analysis_has_no_candidates(self) -> None:
        analysis = {
            **self.analysis,
            "has_event": False,
            "event_type": "NO_EVENT",
        }
        self.assertEqual(
            rank_event_candidates(analysis, [], window_days=30), []
        )

    def test_limit_is_applied_after_ranking(self) -> None:
        events = [
            {
                "event_id": event_id,
                "status": "ACTIVE",
                "canonical_title": f"Event {event_id}",
                "event_type": "GOVERNMENT_CONTRACT",
                "event_date": "2026-08-18",
                "company_ids": [],
                "topic_ids": [],
            }
            for event_id in (1, 2, 3)
        ]
        result = rank_event_candidates(
            self.analysis, events, window_days=30, limit=2
        )
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
