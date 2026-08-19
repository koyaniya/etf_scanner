from __future__ import annotations

import unittest
from unittest.mock import patch

from agents.event_cluster_agent import EventClusterDecision
from processing.event_candidates import EventCandidate
from processing.event_llm_clustering import run_llm_event_clustering_batch


def candidate() -> EventCandidate:
    return EventCandidate(
        event_id=4, canonical_title="Existing", event_type="PARTNERSHIP",
        event_date=None, company_ids=[1], topic_ids=[2], company_overlap=1,
        topic_overlap=1, days_apart=1, similarity_score=0.8,
    )


class EventLlmClusteringTests(unittest.TestCase):
    def test_empty_ambiguous_queue_makes_no_model_call(self) -> None:
        comparator = unittest.mock.Mock()
        with patch(
            "processing.event_llm_clustering.load_ambiguous_clustering_runs",
            return_value=[],
        ):
            result = run_llm_event_clustering_batch(
                object(), limit=5, model="test", comparator=comparator
            )
        comparator.assert_not_called()
        self.assertEqual(result["selected"], 0)

    def test_preview_returns_match_without_writes(self) -> None:
        comparison = EventClusterDecision(
            decision="MATCHED", selected_event_id=4, confidence=0.9,
            reason="Same occurrence.", matching_evidence=[], conflicting_evidence=[],
        )
        with (
            patch("processing.event_llm_clustering.load_ambiguous_clustering_runs",
                  return_value=[{"clustering_run_id": 3, "analysis_id": 8}]),
            patch("processing.event_llm_clustering.find_event_candidates",
                  return_value=({"analysis_id": 8}, [candidate()])),
            patch("processing.event_llm_clustering.mark_llm_run_started") as start,
        ):
            result = run_llm_event_clustering_batch(
                object(), limit=1, model="test",
                comparator=lambda *args, **kwargs: comparison,
            )
        start.assert_not_called()
        self.assertEqual(result["results"][0]["selected_event_id"], 4)

    def test_save_new_event_links_and_finishes(self) -> None:
        comparison = EventClusterDecision(
            decision="NEW_EVENT", selected_event_id=None, confidence=0.8,
            reason="Different award.", matching_evidence=[],
            conflicting_evidence=["Different date"],
        )
        with (
            patch("processing.event_llm_clustering.load_ambiguous_clustering_runs",
                  return_value=[{"clustering_run_id": 3, "analysis_id": 8}]),
            patch("processing.event_llm_clustering.find_event_candidates",
                  return_value=({"analysis_id": 8}, [candidate()])),
            patch("processing.event_llm_clustering.mark_llm_run_started"),
            patch("processing.event_llm_clustering.create_canonical_event",
                  return_value=5),
            patch("processing.event_llm_clustering.link_analysis_to_event") as link,
            patch("processing.event_llm_clustering.finish_llm_run") as finish,
        ):
            result = run_llm_event_clustering_batch(
                object(), limit=1, save=True, model="test",
                comparator=lambda *args, **kwargs: comparison,
            )
        link.assert_called_once()
        finish.assert_called_once()
        self.assertEqual(result["results"][0]["selected_event_id"], 5)


if __name__ == "__main__":
    unittest.main()
