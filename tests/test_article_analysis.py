from __future__ import annotations

import unittest
from unittest.mock import patch

from agents.article_analyzer_schema import ArticleAnalysisOutput
from processing.article_analysis import (
    run_article_analysis_batch,
    select_articles_for_analysis,
)


def analysis_result() -> ArticleAnalysisOutput:
    return ArticleAnalysisOutput.model_validate({
        "has_event": True,
        "event_type": "PARTNERSHIP",
        "impact_direction": "POSITIVE",
        "impact_strength": 3,
        "importance_score": 70,
        "confidence": 0.85,
        "companies": ["Rocket Lab"],
        "topics": ["Launch services"],
        "time_horizon": "MEDIUM_TERM",
        "event_date": None,
        "summary": "The companies announced a partnership.",
        "industry_implication": "Launch capacity may expand.",
        "facts": ["A partnership was announced."],
        "risk_factors": [],
    })


class ArticleAnalysisTests(unittest.TestCase):
    def test_selection_uses_latest_row_and_skips_success(self) -> None:
        rows = [
            {"article_id": 1, "relevance_class": "IRRELEVANT"},
            {"article_id": 1, "relevance_class": "DIRECT_HOLDING"},
            {"article_id": 2, "relevance_class": "DIRECT_HOLDING"},
            {"article_id": 3, "relevance_class": "WEAK_MATCH"},
        ]
        selected = select_articles_for_analysis(rows, {2}, limit=5)
        self.assertEqual([row["article_id"] for row in selected], [3])

    def test_selection_honors_limit(self) -> None:
        rows = [
            {"article_id": 1, "relevance_class": "DIRECT_HOLDING"},
            {"article_id": 2, "relevance_class": "INDUSTRY_RELEVANT"},
        ]
        self.assertEqual(len(select_articles_for_analysis(rows, set(), limit=1)), 1)

    def test_preview_isolates_article_failure(self) -> None:
        candidates = [
            {"article_id": 1, "relevance_id": 11},
            {"article_id": 2, "relevance_id": 12},
        ]

        def analyzer(article, relevance, companies, aliases, topics, model):
            if article["article_id"] == 1:
                raise RuntimeError("model failed")
            return analysis_result()

        with (
            patch("processing.article_analysis.load_analysis_candidates",
                  return_value=candidates),
            patch("processing.article_analysis.load_reference_catalogs",
                  return_value=([], [], [])),
            patch("processing.article_analysis.load_article",
                  side_effect=lambda db, article_id: {"article_id": article_id}),
        ):
            result = run_article_analysis_batch(
                object(), limit=2, model="test-model", analyzer=analyzer
            )

        self.assertEqual(result["mode"], "PREVIEW")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["succeeded"], 1)

    def test_save_records_success_and_failure(self) -> None:
        candidates = [
            {"article_id": 1, "relevance_id": 11},
            {"article_id": 2, "relevance_id": 12},
        ]

        def analyzer(article, relevance, companies, aliases, topics, model):
            if article["article_id"] == 2:
                raise RuntimeError("bad response")
            return analysis_result()

        with (
            patch("processing.article_analysis.load_analysis_candidates",
                  return_value=candidates),
            patch("processing.article_analysis.load_reference_catalogs",
                  return_value=([], [], [])),
            patch("processing.article_analysis.load_article",
                  side_effect=lambda db, article_id: {"article_id": article_id}),
            patch("processing.article_analysis.start_analysis",
                  side_effect=[101, 102]) as start,
            patch("processing.article_analysis.finish_analysis_success") as success,
            patch("processing.article_analysis.finish_analysis_failure") as failure,
        ):
            result = run_article_analysis_batch(
                object(), limit=2, save=True, model="test-model", analyzer=analyzer
            )

        self.assertEqual(start.call_count, 2)
        success.assert_called_once()
        failure.assert_called_once()
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(result["failed"], 1)


if __name__ == "__main__":
    unittest.main()
