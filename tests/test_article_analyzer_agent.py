from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from agents.article_analyzer_agent import (
    ARTICLE_ANALYZER_PROMPT_VERSION,
    analyze_article,
    build_article_analysis_input,
)
from agents.article_analyzer_schema import ArticleAnalysisOutput


def valid_analysis() -> ArticleAnalysisOutput:
    return ArticleAnalysisOutput.model_validate({
        "has_event": True,
        "event_type": "GOVERNMENT_CONTRACT",
        "impact_direction": "POSITIVE",
        "impact_strength": 4,
        "importance_score": 82,
        "confidence": 0.86,
        "companies": ["Rocket Lab USA, Inc."],
        "topics": ["Launch services"],
        "time_horizon": "MEDIUM_TERM",
        "event_date": None,
        "summary": "Rocket Lab received a government contract.",
        "industry_implication": "Government launch demand remains strong.",
        "facts": ["A government contract was announced."],
        "risk_factors": ["The RSS summary omits contract details."],
    })


class FakeResponses:
    def __init__(self, result):
        self.result = result
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=self.result)


class RetryResponses:
    def __init__(self):
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            ArticleAnalysisOutput.model_validate({
                **valid_analysis().model_dump(),
                "impact_strength": None,
            })
        return SimpleNamespace(output_parsed=valid_analysis())


class ArticleAnalyzerAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.article = {
            "article_id": 12,
            "title": "Rocket Lab wins contract",
            "summary": "The company announced a government launch contract.",
            "published_at": "2026-08-19T08:00:00+00:00",
            "source": "Example News",
            "url": "https://example.com/article",
        }
        self.relevance = {
            "relevance_class": "DIRECT_HOLDING",
            "relevance_score": 60,
            "matched_company_ids": [7],
            "matched_aliases": ["Rocket Lab"],
            "matched_topic_ids": [3],
            "matched_keywords": ["launch contract"],
        }
        self.companies = [{
            "company_id": 7,
            "canonical_name": "Rocket Lab USA, Inc.",
            "primary_ticker": "RKLB",
        }]
        self.aliases = [{"company_id": 7, "alias": "Rocket Lab"}]
        self.topics = [{"topic_id": 3, "topic_name": "Launch services"}]

    def test_input_contains_evidence_and_catalogs(self) -> None:
        payload = json.loads(build_article_analysis_input(
            self.article, self.relevance, self.companies, self.aliases, self.topics
        ))
        self.assertEqual(payload["input_type"], "RSS_SUMMARY")
        self.assertEqual(payload["article"]["title"], self.article["title"])
        self.assertEqual(payload["company_catalog"][0]["aliases"], ["Rocket Lab"])
        self.assertEqual(payload["topic_catalog"][0]["topic_name"], "Launch services")

    def test_uses_structured_output_without_web_search(self) -> None:
        responses = FakeResponses(valid_analysis())
        result = analyze_article(
            self.article, self.relevance, self.companies, self.aliases, self.topics,
            client=SimpleNamespace(responses=responses), model="test-model",
        )
        self.assertTrue(result.has_event)
        self.assertEqual(responses.kwargs["model"], "test-model")
        self.assertIs(responses.kwargs["text_format"], ArticleAnalysisOutput)
        self.assertNotIn("tools", responses.kwargs)

    def test_missing_structured_output_raises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no structured article analysis"):
            analyze_article(
                self.article, self.relevance, self.companies, self.aliases, self.topics,
                client=SimpleNamespace(responses=FakeResponses(None)),
                model="test-model",
            )

    def test_prompt_version_is_explicit(self) -> None:
        self.assertEqual(ARTICLE_ANALYZER_PROMPT_VERSION, "article-analyzer-v1")

    def test_validation_error_is_retried_once(self) -> None:
        responses = RetryResponses()
        result = analyze_article(
            self.article, self.relevance, self.companies, self.aliases, self.topics,
            client=SimpleNamespace(responses=responses), model="test-model",
        )
        self.assertTrue(result.has_event)
        self.assertEqual(responses.calls, 2)


if __name__ == "__main__":
    unittest.main()
