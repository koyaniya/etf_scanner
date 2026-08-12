from __future__ import annotations

import unittest
from types import SimpleNamespace

from agents.company_alias_agent import (
    AliasSuggestion,
    CompanyAliasResearch,
    build_pending_alias_rows,
    is_public_evidence_url,
    research_company_aliases,
)


class FakeResponses:
    def __init__(self, parsed: CompanyAliasResearch) -> None:
        self.parsed = parsed
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.parsed)


class FakeOpenAI:
    def __init__(self, parsed: CompanyAliasResearch) -> None:
        self.responses = FakeResponses(parsed)


def make_research() -> CompanyAliasResearch:
    return CompanyAliasResearch(
        company_verified=True,
        company_summary="Verified public space company.",
        aliases=[
            AliasSuggestion(
                alias=" Electron ",
                alias_type="PRODUCT",
                confidence=0.97,
                source_urls=["https://example.com/electron"],
                evidence_summary="Official product page.",
            )
        ],
    )


class CompanyAliasAgentTests(unittest.TestCase):
    def test_research_uses_structured_output_and_web_search(self) -> None:
        client = FakeOpenAI(make_research())

        result = research_company_aliases(
            {
                "company_id": 1,
                "canonical_name": "Rocket Lab USA, Inc.",
                "primary_ticker": "RKLB",
            },
            [],
            client=client,
            model="test-model",
        )

        self.assertTrue(result.company_verified)
        call = client.responses.calls[0]
        self.assertEqual(call["model"], "test-model")
        self.assertEqual(call["tools"], [{"type": "web_search"}])
        self.assertIs(call["text_format"], CompanyAliasResearch)

    def test_pending_rows_are_inactive_and_keep_provenance(self) -> None:
        rows = build_pending_alias_rows(12, make_research(), [])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["alias"], "Electron")
        self.assertEqual(rows[0]["verification_status"], "PENDING")
        self.assertEqual(rows[0]["generated_by"], "AI")
        self.assertFalse(rows[0]["is_active"])
        self.assertEqual(
            rows[0]["source_urls"],
            ["https://example.com/electron"],
        )

    def test_existing_alias_is_not_suggested_again(self) -> None:
        rows = build_pending_alias_rows(
            12,
            make_research(),
            [{"alias": "  ELECTRON  "}],
        )
        self.assertEqual(rows, [])

    def test_unverified_company_produces_no_rows(self) -> None:
        research = make_research().model_copy(
            update={"company_verified": False}
        )
        self.assertEqual(build_pending_alias_rows(12, research, []), [])

    def test_invalid_evidence_url_is_rejected(self) -> None:
        research = make_research()
        research.aliases[0].source_urls = ["not-a-url"]

        self.assertEqual(build_pending_alias_rows(12, research, []), [])
        self.assertTrue(is_public_evidence_url("https://example.com/source"))
        self.assertFalse(is_public_evidence_url("file:///tmp/source"))


if __name__ == "__main__":
    unittest.main()
