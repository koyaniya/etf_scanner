from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from agents.company_alias_agent import CompanyAliasResearch
from processing.alias_enrichment import (
    retry_research,
    run_alias_enrichment_batch,
    select_companies_for_enrichment,
)


class AliasEnrichmentTests(unittest.TestCase):
    def test_selection_skips_successful_companies_and_honors_limit(self) -> None:
        companies = [
            {"company_id": 1},
            {"company_id": 2},
            {"company_id": 3},
        ]

        selected = select_companies_for_enrichment(
            companies,
            {1: datetime.now(timezone.utc)},
            limit=1,
            refresh_days=180,
        )

        self.assertEqual(selected, [{"company_id": 2}])

    def test_force_includes_successful_companies(self) -> None:
        companies = [{"company_id": 1}, {"company_id": 2}]
        selected = select_companies_for_enrichment(
            companies,
            {
                1: datetime.now(timezone.utc),
                2: datetime.now(timezone.utc),
            },
            limit=5,
            refresh_days=180,
            force=True,
        )
        self.assertEqual(selected, companies)

    def test_company_is_eligible_after_refresh_period(self) -> None:
        now = datetime(2026, 8, 12, tzinfo=timezone.utc)
        companies = [{"company_id": 1}, {"company_id": 2}]

        selected = select_companies_for_enrichment(
            companies,
            {
                1: now - timedelta(days=179),
                2: now - timedelta(days=180),
            },
            limit=5,
            refresh_days=180,
            now=now,
        )

        self.assertEqual(selected, [{"company_id": 2}])

    def test_retry_research_retries_then_succeeds(self) -> None:
        attempts = 0
        sleeps: list[float] = []

        def research():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("temporary")
            return "result"

        result, attempt_count = retry_research(
            research,
            max_attempts=3,
            retry_delay_seconds=2,
            sleep=sleeps.append,
        )

        self.assertEqual(result, "result")
        self.assertEqual(attempt_count, 3)
        self.assertEqual(sleeps, [2, 4])

    def test_preview_batch_isolates_company_failure(self) -> None:
        companies = [
            {
                "company_id": 1,
                "canonical_name": "First Company",
                "website_url": "https://first.example",
            },
            {
                "company_id": 2,
                "canonical_name": "Second Company",
                "website_url": "https://second.example",
            },
        ]

        def research(company, existing_aliases, model):
            if company["company_id"] == 1:
                raise RuntimeError("research failed")
            return CompanyAliasResearch(
                company_verified=True,
                company_summary="Verified.",
                aliases=[],
            )

        with (
            patch(
                "processing.alias_enrichment."
                "load_companies_for_alias_enrichment",
                return_value=companies,
            ),
            patch(
                "processing.alias_enrichment."
                "load_latest_successful_research_dates",
                return_value={},
            ),
            patch(
                "processing.alias_enrichment.load_aliases",
                return_value=[],
            ),
        ):
            result = run_alias_enrichment_batch(
                object(),
                limit=2,
                save=False,
                model="test-model",
                max_attempts=1,
                retry_delay_seconds=0,
                research_function=research,
            )

        self.assertEqual(result["mode"], "PREVIEW")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(
            [row["status"] for row in result["results"]],
            ["FAILED", "SUCCESS"],
        )

    def test_preview_batch_shares_candidates_between_companies(self) -> None:
        companies = [
            {
                "company_id": 1,
                "canonical_name": "Example One, Inc.",
                "website_url": "https://one.example",
            },
            {
                "company_id": 2,
                "canonical_name": "Example Two, Inc.",
                "website_url": "https://two.example",
            },
        ]

        from agents.company_alias_agent import AliasSuggestion

        def research(company, existing_aliases, model):
            return CompanyAliasResearch(
                company_verified=True,
                company_summary="Verified.",
                aliases=[
                    AliasSuggestion(
                        alias="Shared Product",
                        alias_type="PRODUCT",
                        confidence=0.99,
                        source_urls=[company["website_url"]],
                        evidence_summary="Official product page.",
                    )
                ],
            )

        with (
            patch(
                "processing.alias_enrichment."
                "load_companies_for_alias_enrichment",
                return_value=companies,
            ),
            patch(
                "processing.alias_enrichment."
                "load_latest_successful_research_dates",
                return_value={},
            ),
            patch(
                "processing.alias_enrichment.load_aliases",
                return_value=[],
            ),
        ):
            result = run_alias_enrichment_batch(
                object(),
                limit=2,
                model="test-model",
                max_attempts=1,
                retry_delay_seconds=0,
                research_function=research,
            )

        second_reasons = result["results"][1]["validation_results"][0][
            "reasons"
        ]
        self.assertIn("ALIAS_USED_BY_ANOTHER_COMPANY", second_reasons)


if __name__ == "__main__":
    unittest.main()
