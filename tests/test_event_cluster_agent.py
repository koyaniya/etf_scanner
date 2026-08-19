from __future__ import annotations

import unittest
from types import SimpleNamespace

from agents.event_cluster_agent import (
    EventClusterDecision,
    compare_ambiguous_event,
)
from processing.event_candidates import EventCandidate


def candidate(event_id: int = 4) -> EventCandidate:
    return EventCandidate(
        event_id=event_id,
        canonical_title="NRO expands data access",
        event_type="GOVERNMENT_CONTRACT",
        event_date="2026-08-18",
        company_ids=[60], topic_ids=[7, 9], company_overlap=1,
        topic_overlap=1, days_apart=0, similarity_score=1,
    )


class FakeResponses:
    def __init__(self, result):
        self.result = result
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=self.result)


class EventClusterAgentTests(unittest.TestCase):
    def test_structured_comparison_uses_no_web_search(self) -> None:
        responses = FakeResponses(EventClusterDecision(
            decision="MATCHED", selected_event_id=4, confidence=0.95,
            reason="Same award.", matching_evidence=["Same company"],
            conflicting_evidence=[],
        ))
        result = compare_ambiguous_event(
            {"analysis_id": 9, "summary": "Award announced."},
            [candidate()],
            client=SimpleNamespace(responses=responses), model="test-model",
        )
        self.assertEqual(result.selected_event_id, 4)
        self.assertNotIn("tools", responses.kwargs)
        self.assertIs(responses.kwargs["text_format"], EventClusterDecision)

    def test_unknown_selected_event_is_rejected(self) -> None:
        responses = FakeResponses(EventClusterDecision(
            decision="MATCHED", selected_event_id=999, confidence=0.8,
            reason="Claimed match.", matching_evidence=[], conflicting_evidence=[],
        ))
        with self.assertRaisesRegex(RuntimeError, "not a supplied candidate"):
            compare_ambiguous_event(
                {"analysis_id": 9}, [candidate()],
                client=SimpleNamespace(responses=responses), model="test-model",
            )

    def test_candidate_is_required(self) -> None:
        with self.assertRaises(ValueError):
            compare_ambiguous_event({"analysis_id": 9}, [], client=object())


if __name__ == "__main__":
    unittest.main()
