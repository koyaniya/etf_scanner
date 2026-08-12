from __future__ import annotations

import unittest

from collectors.company_sync import (
    build_deterministic_aliases,
    derive_short_name,
    filter_missing_aliases,
    is_safe_ticker_alias,
    normalize_alias_key,
    normalize_name,
    normalize_ticker,
)


class CompanyAliasNormalizationTests(unittest.TestCase):
    def test_normalize_name_collapses_unicode_and_whitespace(self) -> None:
        self.assertEqual(
            normalize_name("  Rocket\u3000 Lab,   Inc. "),
            "Rocket Lab, Inc.",
        )

    def test_alias_key_is_case_and_space_insensitive(self) -> None:
        self.assertEqual(
            normalize_alias_key("  ROCKET   Lab "),
            normalize_alias_key("rocket lab"),
        )

    def test_ticker_is_normalized(self) -> None:
        self.assertEqual(normalize_ticker("  rklb "), "RKLB")

    def test_unsafe_tickers_are_rejected(self) -> None:
        self.assertFalse(is_safe_ticker_alias("A"))
        self.assertFalse(is_safe_ticker_alias("BAD TICKER"))
        self.assertFalse(is_safe_ticker_alias(None))
        self.assertTrue(is_safe_ticker_alias("RKLB"))
        self.assertTrue(is_safe_ticker_alias("BRK.B"))


class DeterministicAliasTests(unittest.TestCase):
    def test_legal_suffix_is_removed(self) -> None:
        self.assertEqual(
            derive_short_name("Rocket Lab USA, Inc."),
            "Rocket Lab USA",
        )
        self.assertEqual(
            derive_short_name("Example Holdings Limited PLC"),
            "Example Holdings",
        )

    def test_additional_international_legal_suffixes_are_removed(self) -> None:
        examples = {
            "Example Europe SE": "Example Europe",
            "Example Sweden AB": "Example Sweden",
            "Example Americas SACA": "Example Americas",
            "Example Italia SpA": "Example Italia",
        }

        for canonical_name, expected in examples.items():
            with self.subTest(canonical_name=canonical_name):
                self.assertEqual(derive_short_name(canonical_name), expected)

    def test_source_name_artifacts_are_removed_separately(self) -> None:
        examples = {
            "Example Aerospace Ltd/Korea": "Example Aerospace",
            "Example Satellite Co/Japan": "Example Satellite",
            "Example Aircraft Co/The": "Example Aircraft",
        }

        for canonical_name, expected in examples.items():
            with self.subTest(canonical_name=canonical_name):
                self.assertEqual(derive_short_name(canonical_name), expected)

    def test_holdings_is_not_removed(self) -> None:
        self.assertIsNone(derive_short_name("Example Holdings"))
        self.assertEqual(
            derive_short_name("Example Holdings PLC"),
            "Example Holdings",
        )

    def test_name_without_legal_suffix_has_no_short_variant(self) -> None:
        self.assertIsNone(derive_short_name("SpaceX"))

    def test_generic_short_name_is_rejected(self) -> None:
        self.assertIsNone(derive_short_name("Space, Inc."))

    def test_candidates_have_metadata_and_are_deduplicated(self) -> None:
        aliases = build_deterministic_aliases(
            [
                {
                    "company_id": 42,
                    "canonical_name": "  Example Space, Inc. ",
                    "primary_ticker": " exsp ",
                }
            ]
        )

        self.assertEqual(
            [(row["alias"], row["alias_type"]) for row in aliases],
            [
                ("Example Space, Inc.", "OFFICIAL_NAME"),
                ("EXSP", "TICKER"),
                ("Example Space", "SHORT_NAME"),
            ],
        )
        self.assertTrue(all(row["is_active"] for row in aliases))
        self.assertTrue(
            all(row["verification_status"] == "VERIFIED" for row in aliases)
        )
        self.assertTrue(
            all(row["generated_by"] == "DETERMINISTIC" for row in aliases)
        )

    def test_same_alias_is_not_added_twice_for_company(self) -> None:
        aliases = build_deterministic_aliases(
            [
                {
                    "company_id": 7,
                    "canonical_name": "ABCD Inc.",
                    "primary_ticker": "abcd inc.",
                }
            ]
        )

        self.assertEqual(len(aliases), 2)
        self.assertEqual(
            {row["alias_type"] for row in aliases},
            {"OFFICIAL_NAME", "SHORT_NAME"},
        )

    def test_existing_case_and_whitespace_variant_is_skipped(self) -> None:
        candidates = [
            {
                "company_id": 9,
                "alias": "Rocket Lab",
                "alias_type": "SHORT_NAME",
            },
            {
                "company_id": 9,
                "alias": "RKLB",
                "alias_type": "TICKER",
            },
        ]

        missing = filter_missing_aliases(
            candidates,
            [{"company_id": 9, "alias": "  ROCKET   LAB "}],
        )

        self.assertEqual(missing, [candidates[1]])


if __name__ == "__main__":
    unittest.main()
