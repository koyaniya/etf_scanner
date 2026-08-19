from __future__ import annotations

import unittest
from unittest.mock import patch

from agents.event_consolidation_agent import EventConsolidation
from processing.event_updates import (
    evidence_signature,
    run_event_update_batch,
)


def consolidation(material: bool) -> EventConsolidation:
    return EventConsolidation.model_validate({
        "has_material_update": material,
        "canonical_title": "Canonical event",
        "event_type": "PARTNERSHIP",
        "impact_direction": "POSITIVE",
        "impact_strength": 3,
        "importance_score": 65,
        "confidence": 0.85,
        "time_horizon": "MEDIUM_TERM",
        "event_date": None,
        "canonical_summary": "Two companies formed a partnership.",
        "industry_implication": None,
        "facts": ["A partnership was announced."],
        "risk_factors": [],
        "new_information": [],
        "contradictions": [],
        "change_summary": "No material change." if not material else "Added terms.",
    })


def pending() -> dict:
    return {
        "event": {"event_id": 4, "canonical_title": "Existing"},
        "analysis_ids": [1, 2],
        "triggering_analysis_id": 2,
        "evidence_signature": evidence_signature([1, 2]),
    }


class EventUpdateTests(unittest.TestCase):
    def test_signature_is_order_independent_and_deduplicated(self) -> None:
        self.assertEqual(
            evidence_signature([2, 1, 2]), evidence_signature([1, 2])
        )

    def test_empty_queue_avoids_consolidator(self) -> None:
        consolidator = unittest.mock.Mock()
        with patch(
            "processing.event_updates.load_pending_event_updates",
            return_value=[],
        ):
            result = run_event_update_batch(
                object(), limit=5, model="test", consolidator=consolidator
            )
        consolidator.assert_not_called()
        self.assertEqual(result["selected"], 0)

    def test_preview_reports_update_without_writes(self) -> None:
        with (
            patch("processing.event_updates.load_pending_event_updates",
                  return_value=[pending()]),
            patch("processing.event_updates.load_event_analyses",
                  return_value=[{"analysis_id": 1}, {"analysis_id": 2}]),
            patch("processing.event_updates.start_event_update") as start,
        ):
            result = run_event_update_batch(
                object(), limit=1, model="test",
                consolidator=lambda *args, **kwargs: consolidation(True),
            )
        start.assert_not_called()
        self.assertEqual(result["updated"], 1)

    def test_save_no_change_still_finishes_audit(self) -> None:
        with (
            patch("processing.event_updates.load_pending_event_updates",
                  return_value=[pending()]),
            patch("processing.event_updates.load_event_analyses",
                  return_value=[{"analysis_id": 1}, {"analysis_id": 2}]),
            patch("processing.event_updates.start_event_update",
                  return_value=8),
            patch("processing.event_updates.finish_event_update",
                  return_value="NO_CHANGE") as finish,
        ):
            result = run_event_update_batch(
                object(), limit=1, save=True, model="test",
                consolidator=lambda *args, **kwargs: consolidation(False),
            )
        finish.assert_called_once()
        self.assertEqual(result["unchanged"], 1)


if __name__ == "__main__":
    unittest.main()
