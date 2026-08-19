from __future__ import annotations

import unittest
from unittest.mock import patch

from processing.event_candidates import EventCandidate
from processing.event_clustering import (
    decide_deterministic_cluster,
    run_event_clustering_batch,
)


def candidate(
    event_id: int,
    score: float,
    *,
    company_overlap: float = 1.0,
) -> EventCandidate:
    return EventCandidate(
        event_id=event_id,
        canonical_title=f"Event {event_id}",
        event_type="GOVERNMENT_CONTRACT",
        event_date="2026-08-18",
        company_ids=[60],
        topic_ids=[7, 9],
        company_overlap=company_overlap,
        topic_overlap=1.0,
        days_apart=0,
        similarity_score=score,
    )


class EventClusteringTests(unittest.TestCase):
    def test_no_candidates_creates_new_event(self) -> None:
        decision = decide_deterministic_cluster([])
        self.assertEqual(decision.decision, "NEW_EVENT")

    def test_strong_separated_company_match_is_automatic(self) -> None:
        decision = decide_deterministic_cluster([
            candidate(1, 0.95),
            candidate(2, 0.60),
        ])
        self.assertEqual(decision.decision, "MATCHED")
        self.assertEqual(decision.selected_event_id, 1)

    def test_strong_score_without_company_is_not_automatic(self) -> None:
        decision = decide_deterministic_cluster([
            candidate(1, 0.90, company_overlap=0),
        ])
        self.assertEqual(decision.decision, "AMBIGUOUS")

    def test_two_close_candidates_are_ambiguous(self) -> None:
        decision = decide_deterministic_cluster([
            candidate(1, 0.95), candidate(2, 0.90)
        ])
        self.assertEqual(decision.decision, "AMBIGUOUS")

    def test_low_score_creates_new_event(self) -> None:
        decision = decide_deterministic_cluster([candidate(1, 0.40)])
        self.assertEqual(decision.decision, "NEW_EVENT")

    def test_preview_does_not_start_persistent_run(self) -> None:
        analysis = {"analysis_id": 1}
        with (
            patch("processing.event_clustering.load_unclustered_analyses",
                  return_value=[1]),
            patch("processing.event_clustering.find_event_candidates",
                  return_value=(analysis, [])),
            patch("processing.event_clustering.start_clustering_run") as start,
        ):
            result = run_event_clustering_batch(object(), limit=1)
        start.assert_not_called()
        self.assertEqual(result["new_events"], 1)

    def test_save_creates_event_links_and_finishes_run(self) -> None:
        analysis = {"analysis_id": 1, "company_ids": [], "topic_ids": []}
        with (
            patch("processing.event_clustering.load_unclustered_analyses",
                  return_value=[1]),
            patch("processing.event_clustering.find_event_candidates",
                  return_value=(analysis, [])),
            patch("processing.event_clustering.start_clustering_run",
                  return_value=11),
            patch("processing.event_clustering.create_canonical_event",
                  return_value=21) as create,
            patch("processing.event_clustering.link_analysis_to_event") as link,
            patch("processing.event_clustering.finish_clustering_run") as finish,
        ):
            result = run_event_clustering_batch(
                object(), limit=1, save=True
            )
        create.assert_called_once()
        link.assert_called_once()
        finish.assert_called_once()
        self.assertEqual(result["results"][0]["selected_event_id"], 21)

    def test_ambiguous_save_records_run_without_linking(self) -> None:
        analysis = {"analysis_id": 1}
        with (
            patch("processing.event_clustering.load_unclustered_analyses",
                  return_value=[1]),
            patch("processing.event_clustering.find_event_candidates",
                  return_value=(analysis, [candidate(1, 0.70)])),
            patch("processing.event_clustering.start_clustering_run",
                  return_value=11),
            patch("processing.event_clustering.link_analysis_to_event") as link,
            patch("processing.event_clustering.finish_clustering_run") as finish,
        ):
            result = run_event_clustering_batch(
                object(), limit=1, save=True
            )
        link.assert_not_called()
        finish.assert_called_once()
        self.assertEqual(result["ambiguous"], 1)


if __name__ == "__main__":
    unittest.main()
