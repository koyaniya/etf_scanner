from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from agents.company_alias_agent import (
    AliasSuggestion,
    CompanyAliasResearch,
    is_public_evidence_url,
)
from collectors.company_sync import (
    GENERIC_SHORT_NAMES,
    is_safe_ticker_alias,
    normalize_alias_key,
    normalize_name,
)


ValidationDecision = Literal["AUTO_APPROVE", "REJECT"]

MINIMUM_CONFIDENCE = {
    "SHORT_NAME": 0.90,
    "TICKER": 0.95,
    "FORMER_NAME": 0.90,
    "PRODUCT": 0.85,
    "BRAND": 0.85,
    "SUBSIDIARY": 0.90,
}

RELATIONSHIP_ALIAS_TYPES = {"PRODUCT", "BRAND", "SUBSIDIARY"}
GENERIC_ALIAS_TERMS = GENERIC_SHORT_NAMES | {
    "aircraft",
    "defense",
    "group",
    "launch",
    "satellite",
    "services",
    "systems",
}


@dataclass(frozen=True)
class AliasValidationResult:
    alias: str
    alias_type: str
    confidence: float
    decision: ValidationDecision
    reasons: list[str]
    database_row: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def hostname(value: str | None) -> str | None:
    if not value:
        return None

    parsed = urlparse(value.strip())
    host = parsed.hostname
    if not host:
        return None

    return host.casefold().removeprefix("www.")


def source_matches_company_website(
    source_urls: list[str],
    company_website_url: str | None,
) -> bool:
    company_host = hostname(company_website_url)
    if not company_host:
        return False

    for source_url in source_urls:
        source_host = hostname(source_url)
        if source_host == company_host:
            return True
        if source_host and source_host.endswith(f".{company_host}"):
            return True

    return False


def is_malformed_alias(alias: str, alias_type: str) -> bool:
    if not alias or len(alias) > 120:
        return True
    if any(ord(character) < 32 for character in alias):
        return True
    if "://" in alias or alias.casefold().startswith("www."):
        return True

    alphanumeric_length = len(re.sub(r"[^\w]", "", alias))
    if alphanumeric_length < 3:
        return True

    if alias_type == "TICKER" and not is_safe_ticker_alias(alias.upper()):
        return True

    return False


def is_generic_alias(alias: str) -> bool:
    return normalize_alias_key(alias) in GENERIC_ALIAS_TERMS


def is_canonical_short_name(
    alias: str,
    canonical_name: str | None,
) -> bool:
    """Check whether alias tokens occur contiguously in the canonical name."""

    alias_tokens = re.findall(r"\w+", normalize_alias_key(alias))
    canonical_tokens = re.findall(
        r"\w+",
        normalize_alias_key(canonical_name),
    )

    if not alias_tokens or len(alias_tokens) >= len(canonical_tokens):
        return False
    if len(alias_tokens) == 1 and len(alias_tokens[0]) < 6:
        return False

    width = len(alias_tokens)
    return any(
        canonical_tokens[index:index + width] == alias_tokens
        for index in range(len(canonical_tokens) - width + 1)
    )


def build_alias_database_row(
    company_id: int,
    suggestion: AliasSuggestion,
    alias: str,
    source_urls: list[str],
    decision: ValidationDecision,
    reasons: list[str],
) -> dict[str, Any]:
    validation_note = ", ".join(reasons)
    evidence_note = normalize_name(suggestion.evidence_summary)

    return {
        "company_id": company_id,
        "alias": alias,
        "alias_type": suggestion.alias_type,
        "is_active": True,
        "confidence": suggestion.confidence,
        "verification_status": "VERIFIED",
        "source_urls": source_urls,
        "generated_by": "AI",
        "notes": f"{evidence_note} Validation: {validation_note}",
    }


def validate_alias_suggestions(
    company: dict[str, Any],
    research: CompanyAliasResearch,
    existing_company_aliases: list[dict[str, Any]],
    all_company_aliases: list[dict[str, Any]],
) -> list[AliasValidationResult]:
    """Apply deterministic safety policy to researched alias suggestions."""

    if not research.company_verified:
        return []

    company_id = company["company_id"]
    same_company_keys = {
        normalize_alias_key(row.get("alias"))
        for row in existing_company_aliases
        if row.get("alias")
    }
    seen_candidate_keys: set[str] = set()
    results: list[AliasValidationResult] = []

    for suggestion in research.aliases:
        alias = normalize_name(suggestion.alias)
        if suggestion.alias_type == "TICKER":
            alias = alias.upper()
        alias_key = normalize_alias_key(alias)
        source_urls = list(
            dict.fromkeys(url.strip() for url in suggestion.source_urls)
        )
        rejection_reasons: list[str] = []
        review_reasons: list[str] = []

        if alias_key in same_company_keys or alias_key in seen_candidate_keys:
            rejection_reasons.append("DUPLICATE_FOR_COMPANY")
        if is_malformed_alias(suggestion.alias, suggestion.alias_type):
            rejection_reasons.append("MALFORMED_ALIAS")
        if is_generic_alias(alias):
            rejection_reasons.append("GENERIC_ALIAS")
        if not source_urls or not all(
            is_public_evidence_url(url) for url in source_urls
        ):
            rejection_reasons.append("INVALID_EVIDENCE_URL")

        minimum_confidence = MINIMUM_CONFIDENCE[suggestion.alias_type]
        if suggestion.confidence < minimum_confidence:
            rejection_reasons.append("CONFIDENCE_BELOW_MINIMUM")

        conflicting_company_ids = {
            row.get("company_id")
            for row in all_company_aliases
            if row.get("company_id") != company_id
            and normalize_alias_key(row.get("alias")) == alias_key
        }
        if conflicting_company_ids:
            review_reasons.append("ALIAS_USED_BY_ANOTHER_COMPANY")

        has_official_source = source_matches_company_website(
            source_urls,
            company.get("website_url"),
        )

        if suggestion.alias_type in RELATIONSHIP_ALIAS_TYPES:
            review_reasons.append("OWNERSHIP_RELATIONSHIP_REQUIRES_REVIEW")
            if not has_official_source:
                review_reasons.append("NO_COMPANY_WEBSITE_EVIDENCE")
        elif suggestion.alias_type == "FORMER_NAME":
            review_reasons.append("CORPORATE_LINEAGE_REQUIRES_REVIEW")
        elif suggestion.alias_type == "TICKER":
            review_reasons.append("TICKER_REQUIRES_REVIEW")
        elif suggestion.alias_type == "SHORT_NAME":
            if not is_canonical_short_name(
                alias,
                company.get("canonical_name"),
            ):
                review_reasons.append("NOT_DERIVED_FROM_CANONICAL_NAME")
            if not has_official_source:
                review_reasons.append("NO_COMPANY_WEBSITE_EVIDENCE")
            if suggestion.confidence < 0.98:
                review_reasons.append("CONFIDENCE_BELOW_AUTO_APPROVAL")

        seen_candidate_keys.add(alias_key)

        if rejection_reasons:
            results.append(
                AliasValidationResult(
                    alias=alias,
                    alias_type=suggestion.alias_type,
                    confidence=suggestion.confidence,
                    decision="REJECT",
                    reasons=rejection_reasons + review_reasons,
                    database_row=None,
                )
            )
            continue

        decision: ValidationDecision = "AUTO_APPROVE"
        reasons = review_reasons or ["SAFE_CANONICAL_SHORT_NAME"]
        database_row = build_alias_database_row(
            company_id,
            suggestion,
            alias,
            source_urls,
            decision,
            reasons,
        )
        results.append(
            AliasValidationResult(
                alias=alias,
                alias_type=suggestion.alias_type,
                confidence=suggestion.confidence,
                decision=decision,
                reasons=reasons,
                database_row=database_row,
            )
        )

    return results


def database_rows_from_validation(
    results: list[AliasValidationResult],
) -> list[dict[str, Any]]:
    return [
        result.database_row
        for result in results
        if result.database_row is not None
    ]
