from __future__ import annotations

import unittest

from agents.company_alias_agent import AliasSuggestion, CompanyAliasResearch
from processing.alias_validator import (
    database_rows_from_validation,
    is_canonical_short_name,
    source_matches_company_website,
    validate_alias_suggestions,
)


COMPANY = {
    "company_id": 10,
    "canonical_name": "Rocket Lab USA, Inc.",
    "primary_ticker": "RKLB",
    "website_url": "https://www.rocketlabusa.com",
}


def research_with(
    alias: str,
    alias_type: str,
    confidence: float = 0.99,
    source_url: str = "https://rocketlabusa.com/about",
) -> CompanyAliasResearch:
    return CompanyAliasResearch(
        company_verified=True,
        company_summary="Verified company.",
        aliases=[
            AliasSuggestion(
                alias=alias,
                alias_type=alias_type,
                confidence=confidence,
                source_urls=[source_url],
                evidence_summary="Evidence for the alias.",
            )
        ],
    )


class AliasValidatorTests(unittest.TestCase):
    def validate(self, research, *, existing=None, all_aliases=None):
        return validate_alias_suggestions(
            COMPANY,
            research,
            existing or [],
            all_aliases or [],
        )[0]

    def test_safe_canonical_short_name_is_auto_approved(self) -> None:
        result = self.validate(research_with("Rocket Lab", "SHORT_NAME"))

        self.assertEqual(result.decision, "AUTO_APPROVE")
        self.assertEqual(result.reasons, ["SAFE_CANONICAL_SHORT_NAME"])
        self.assertTrue(result.database_row["is_active"])
        self.assertEqual(
            result.database_row["verification_status"],
            "VERIFIED",
        )

    def test_short_name_without_official_source_requires_review(self) -> None:
        result = self.validate(
            research_with(
                "Rocket Lab",
                "SHORT_NAME",
                source_url="https://example.com/article",
            )
        )

        self.assertEqual(result.decision, "REVIEW")
        self.assertIn("NO_COMPANY_WEBSITE_EVIDENCE", result.reasons)
        self.assertFalse(result.database_row["is_active"])

    def test_generic_alias_is_rejected(self) -> None:
        result = self.validate(research_with("Space", "SHORT_NAME"))

        self.assertEqual(result.decision, "REJECT")
        self.assertIn("GENERIC_ALIAS", result.reasons)
        self.assertIsNone(result.database_row)

    def test_low_confidence_alias_is_rejected(self) -> None:
        result = self.validate(
            research_with("Photon", "PRODUCT", confidence=0.50)
        )

        self.assertEqual(result.decision, "REJECT")
        self.assertIn("CONFIDENCE_BELOW_MINIMUM", result.reasons)

    def test_cross_company_alias_requires_review(self) -> None:
        result = self.validate(
            research_with("Rocket Lab", "SHORT_NAME"),
            all_aliases=[{"company_id": 99, "alias": "rocket lab"}],
        )

        self.assertEqual(result.decision, "REVIEW")
        self.assertIn("ALIAS_USED_BY_ANOTHER_COMPANY", result.reasons)

    def test_relationship_alias_always_requires_review(self) -> None:
        result = self.validate(research_with("Electron", "PRODUCT"))

        self.assertEqual(result.decision, "REVIEW")
        self.assertIn(
            "OWNERSHIP_RELATIONSHIP_REQUIRES_REVIEW",
            result.reasons,
        )

    def test_subsidiary_alias_requires_review(self) -> None:
        result = self.validate(
            research_with("Example Subsidiary", "SUBSIDIARY")
        )

        self.assertEqual(result.decision, "REVIEW")
        self.assertIn(
            "OWNERSHIP_RELATIONSHIP_REQUIRES_REVIEW",
            result.reasons,
        )

    def test_ticker_is_normalized_and_requires_review(self) -> None:
        result = self.validate(research_with("rklx", "TICKER"))

        self.assertEqual(result.alias, "RKLX")
        self.assertEqual(result.decision, "REVIEW")
        self.assertEqual(result.database_row["alias"], "RKLX")
        self.assertIn("TICKER_REQUIRES_REVIEW", result.reasons)

    def test_duplicate_for_same_company_is_rejected(self) -> None:
        result = self.validate(
            research_with("Rocket Lab", "SHORT_NAME"),
            existing=[{"company_id": 10, "alias": " ROCKET  LAB "}],
        )

        self.assertEqual(result.decision, "REJECT")
        self.assertIn("DUPLICATE_FOR_COMPANY", result.reasons)

    def test_only_reviewable_and_approved_rows_are_returned(self) -> None:
        approved = self.validate(research_with("Rocket Lab", "SHORT_NAME"))
        rejected = self.validate(research_with("Space", "SHORT_NAME"))

        rows = database_rows_from_validation([approved, rejected])
        self.assertEqual(rows, [approved.database_row])

    def test_hostname_and_canonical_relationship_helpers(self) -> None:
        self.assertTrue(
            source_matches_company_website(
                ["https://news.rocketlabusa.com/update"],
                "https://www.rocketlabusa.com",
            )
        )
        self.assertTrue(
            is_canonical_short_name("Rocket Lab", "Rocket Lab USA, Inc.")
        )
        self.assertFalse(
            is_canonical_short_name("Lab", "Rocket Lab USA, Inc.")
        )


if __name__ == "__main__":
    unittest.main()
