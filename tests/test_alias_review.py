from __future__ import annotations

import unittest
from datetime import datetime, timezone

from processing.alias_review import build_review_update


NOW = datetime(2026, 8, 12, 10, 30, tzinfo=timezone.utc)


def alias_row(status: str, active: bool) -> dict:
    return {
        "alias_id": 1,
        "verification_status": status,
        "is_active": active,
        "notes": "Original evidence.",
    }


class AliasReviewTests(unittest.TestCase):
    def test_approve_pending_alias(self) -> None:
        update = build_review_update(
            alias_row("PENDING", False),
            "approve",
            "anna",
            note="Confirmed on official website.",
            reviewed_at=NOW,
        )

        self.assertEqual(update["verification_status"], "VERIFIED")
        self.assertTrue(update["is_active"])
        self.assertEqual(update["reviewed_by"], "anna")
        self.assertIn("APPROVE by anna", update["notes"])
        self.assertIn("Confirmed on official website.", update["notes"])

    def test_reject_pending_alias(self) -> None:
        update = build_review_update(
            alias_row("PENDING", False),
            "reject",
            "reviewer",
            reviewed_at=NOW,
        )

        self.assertEqual(update["verification_status"], "REJECTED")
        self.assertFalse(update["is_active"])

    def test_deactivate_verified_alias(self) -> None:
        update = build_review_update(
            alias_row("VERIFIED", True),
            "deactivate",
            "reviewer",
            reviewed_at=NOW,
        )

        self.assertEqual(update["verification_status"], "VERIFIED")
        self.assertFalse(update["is_active"])

    def test_reopen_rejected_alias(self) -> None:
        update = build_review_update(
            alias_row("REJECTED", False),
            "reopen",
            "reviewer",
            reviewed_at=NOW,
        )

        self.assertEqual(update["verification_status"], "PENDING")
        self.assertFalse(update["is_active"])

    def test_reopen_deactivated_alias(self) -> None:
        update = build_review_update(
            alias_row("VERIFIED", False),
            "reopen",
            "reviewer",
            reviewed_at=NOW,
        )

        self.assertEqual(update["verification_status"], "PENDING")
        self.assertFalse(update["is_active"])

    def test_invalid_transition_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Cannot approve"):
            build_review_update(
                alias_row("REJECTED", False),
                "approve",
                "reviewer",
                reviewed_at=NOW,
            )

    def test_blank_reviewer_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be blank"):
            build_review_update(
                alias_row("PENDING", False),
                "approve",
                "  ",
                reviewed_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
