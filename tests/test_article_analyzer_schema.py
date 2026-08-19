from __future__ import annotations

import unittest

from pydantic import ValidationError

from agents.article_analyzer_schema import ArticleAnalysisOutput
from processing.article_analysis_entities import resolve_analysis_entities


def valid_event(**updates) -> dict:
    result = {
        "has_event": True,
        "event_type": "GOVERNMENT_CONTRACT",
        "impact_direction": "POSITIVE",
        "impact_strength": 4,
        "importance_score": 82,
        "confidence": 0.91,
        "companies": ["Rocket Lab"],
        "topics": ["Launch services"],
        "time_horizon": "MEDIUM_TERM",
        "event_date": "2026-08-19",
        "summary": "Rocket Lab received a government launch contract.",
        "industry_implication": "Government launch demand remains strong.",
        "facts": ["A government launch contract was announced."],
        "risk_factors": [],
    }
    result.update(updates)
    return result


class ArticleAnalyzerSchemaTests(unittest.TestCase):
    def test_valid_event_is_accepted_and_lists_are_normalized(self) -> None:
        result = ArticleAnalysisOutput.model_validate(
            valid_event(
                companies=[" Rocket  Lab ", "rocket lab"],
                facts=[" Contract announced. ", "contract announced."],
            )
        )

        self.assertEqual(result.companies, ["Rocket Lab"])
        self.assertEqual(result.facts, ["Contract announced."])

    def test_real_event_requires_direction_and_strength(self) -> None:
        with self.assertRaises(ValidationError):
            ArticleAnalysisOutput.model_validate(
                valid_event(impact_direction=None)
            )

        with self.assertRaises(ValidationError):
            ArticleAnalysisOutput.model_validate(
                valid_event(impact_strength=None)
            )

    def test_no_event_has_consistent_empty_impact_fields(self) -> None:
        result = ArticleAnalysisOutput.model_validate(
            valid_event(
                has_event=False,
                event_type="NO_EVENT",
                impact_direction=None,
                impact_strength=None,
                event_date=None,
                importance_score=20,
                summary="The article contains background commentary only.",
            )
        )
        self.assertFalse(result.has_event)

    def test_invalid_scores_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ArticleAnalysisOutput.model_validate(
                valid_event(importance_score=101)
            )

        with self.assertRaises(ValidationError):
            ArticleAnalysisOutput.model_validate(valid_event(confidence=1.1))

    def test_entities_resolve_by_canonical_name_alias_and_topic(self) -> None:
        analysis = ArticleAnalysisOutput.model_validate(
            valid_event(
                companies=["Rocket Lab", "RKLB", "Unknown Company"],
                topics=["Launch services", "Unknown Topic"],
            )
        )

        resolved = resolve_analysis_entities(
            analysis,
            companies=[
                {
                    "company_id": 7,
                    "canonical_name": "Rocket Lab USA, Inc.",
                    "primary_ticker": "RKLB",
                }
            ],
            aliases=[{"company_id": 7, "alias": "Rocket Lab"}],
            topics=[{"topic_id": 3, "topic_name": "Launch services"}],
        )

        self.assertEqual(resolved.company_ids, [7])
        self.assertEqual(resolved.topic_ids, [3])
        self.assertEqual(resolved.unresolved_companies, ["Unknown Company"])
        self.assertEqual(resolved.unresolved_topics, ["Unknown Topic"])

    def test_ambiguous_alias_is_not_guessed(self) -> None:
        analysis = ArticleAnalysisOutput.model_validate(valid_event())
        resolved = resolve_analysis_entities(
            analysis,
            companies=[],
            aliases=[
                {"company_id": 1, "alias": "Rocket Lab"},
                {"company_id": 2, "alias": "Rocket Lab"},
            ],
            topics=[],
        )

        self.assertEqual(resolved.company_ids, [])
        self.assertEqual(resolved.ambiguous_companies, ["Rocket Lab"])


if __name__ == "__main__":
    unittest.main()
