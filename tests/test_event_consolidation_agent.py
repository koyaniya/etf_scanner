from __future__ import annotations

import unittest
from types import SimpleNamespace

from agents.event_consolidation_agent import (
    EventConsolidation,
    consolidate_event,
)


def consolidation(material: bool = True) -> EventConsolidation:
    return EventConsolidation.model_validate({
        "has_material_update": material,
        "canonical_title": "NRO expands HawkEye 360 access",
        "event_type": "GOVERNMENT_CONTRACT",
        "impact_direction": "POSITIVE",
        "impact_strength": 3,
        "importance_score": 70,
        "confidence": 0.9,
        "time_horizon": "MEDIUM_TERM",
        "event_date": None,
        "canonical_summary": "The NRO expanded access to RF intelligence data.",
        "industry_implication": "Government demand remains strong.",
        "facts": ["The NRO expanded data access."],
        "risk_factors": ["Contract value was not disclosed."],
        "new_information": ["A second source confirmed the expansion."],
        "contradictions": [],
        "change_summary": "Added confirmation from a second source.",
    })


class FakeResponses:
    def __init__(self, result):
        self.result = result
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=self.result)


class EventConsolidationAgentTests(unittest.TestCase):
    def test_uses_structured_output_without_web_search(self) -> None:
        responses = FakeResponses(consolidation())
        result = consolidate_event(
            {"event_id": 1, "canonical_summary": "Existing."},
            [{"analysis_id": 1}, {"analysis_id": 2}],
            client=SimpleNamespace(responses=responses), model="test-model",
        )
        self.assertTrue(result.has_material_update)
        self.assertIs(responses.kwargs["text_format"], EventConsolidation)
        self.assertNotIn("tools", responses.kwargs)

    def test_requires_multiple_analyses(self) -> None:
        with self.assertRaises(ValueError):
            consolidate_event({}, [{"analysis_id": 1}], client=object())


if __name__ == "__main__":
    unittest.main()
