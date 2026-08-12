from __future__ import annotations

import os

from typing import Any, Literal, Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel


class CompanyClassification(BaseModel):
    entity_type: Literal[
        "COMPANY",
        "FUND",
        "CURRENCY",
        "CASH",
        "BOND",
        "ETF",
        "WARRANT",
        "RIGHT",
        "OTHER",
        "UNKNOWN",
    ]
    confidence: float
    canonical_name: Optional[str]

    verified: bool
    identifier_conflict: bool
    corporate_action_detected: bool

    exchange: Optional[str]
    country_code: Optional[str]
    website_url: Optional[str]

    verification_summary: Optional[str]
    reason: str
    


def create_openai_client() -> OpenAI:
    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    return OpenAI(api_key=api_key)



ALLOWED_ENTITY_TYPES = {
    "COMPANY",
    "FUND",
    "CURRENCY",
    "CASH",
    "BOND",
    "ETF",
    "WARRANT",
    "RIGHT",
    "OTHER",
    "UNKNOWN",
}


def build_company_classification_input(
    holding: dict[str, Any],
) -> dict[str, Any]:
    """
    Prepare the minimum holding information needed
    for LLM-based entity classification.
    """

    return {
        "stock_ticker": holding.get("stock_ticker"),
        "cusip": holding.get("cusip"),
        "security_name": holding.get("security_name"),
        "holding_type": holding.get("holding_type"),
    }


def validate_classification_result(
    result: dict[str, Any],
) -> None:
    """
    Validate the structured result returned by the LLM.
    """

    entity_type = result.get("entity_type")
    confidence = result.get("confidence")

    if entity_type not in ALLOWED_ENTITY_TYPES:
        raise ValueError(
            f"Invalid entity_type returned: {entity_type}"
        )

    if not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be numeric.")

    if not 0 <= confidence <= 1:
        raise ValueError(
            "confidence must be between 0 and 1."
        )


def classify_with_llm(
    holding: dict[str, Any],
) -> dict[str, Any]:
    client = create_openai_client()

    holding_input = build_company_classification_input(
        holding
    )

    response = client.responses.parse(
        model="gpt-5-mini",
        tools=[
        {
            "type": "web_search",
        }
         ],
         input=[
            {
                "role": "system",
                "content": (
                    "Your primary goal is to determine the underlying company/entity for "
                    "the company master, not necessarily whether every identifier refers "
                    "to the exact same current security."

                    "A historical CUSIP, former ticker, renamed security, merger, holding "
                    "company reorganization, share-class change, or other corporate action "
                    "does NOT by itself constitute an identifier conflict if reliable "
                    "sources show that the identifiers belong to the same underlying "
                    "company or its direct corporate successor."

                    "If such a corporate action explains the difference, set "
                    "corporate_action_detected=true, identifier_conflict=false, and "
                    "verified=true when the underlying company can be confidently verified."

                    "Set identifier_conflict=true only when the identifiers appear to "
                    "refer to genuinely different unrelated entities."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Stock ticker: {holding_input['stock_ticker']}\n"
                    f"CUSIP: {holding_input['cusip']}\n"
                    f"Security name: {holding_input['security_name']}"
                ),
            },
        ],
        text_format=CompanyClassification,
        )

    result = response.output_parsed

    if result is None:
        raise RuntimeError(
            "OpenAI returned no structured classification."
        )

    result_dict = result.model_dump()

    validate_classification_result(result_dict)

    return result_dict


def should_accept_classification(
    result: dict[str, Any],
    minimum_confidence: float = 0.90,
    ) -> bool:
    """
    Decide whether an LLM classification is confident enough
    for automatic processing.
    """

    if result["entity_type"] == "UNKNOWN":
        return False

    if not result["verified"]:
        return False

    if result["identifier_conflict"]:
        return False

    return result["confidence"] >= minimum_confidence

def build_company_candidate(
    holding: dict[str, Any],
    llm_result: dict[str, Any],
) -> dict[str, Any]:
    """
    Build a normalized company candidate from an unresolved holding
    and its LLM classification.
    """

    if llm_result["entity_type"] != "COMPANY":
        raise ValueError(
            "Company candidate can only be built from COMPANY classification."
        )

    return {
        "stock_ticker": holding.get("stock_ticker"),
        "cusip": holding.get("cusip"),
        "security_name": holding.get("security_name"),
        "canonical_name": llm_result.get("canonical_name"),
        "exchange": llm_result.get("exchange"),
        "country_code": llm_result.get("country_code"),
        "website_url": llm_result.get("website_url"),
        "confidence": llm_result.get("confidence"),
        "reason": llm_result.get("reason"),
    }


def normalize_company_candidate(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    normalized = candidate.copy()

    exchange = normalized.get("exchange")
    if exchange:
        exchange = exchange.strip().upper()

        # Example: "NASDAQ (RKLB)" -> "NASDAQ"
        if "(" in exchange:
            exchange = exchange.split("(", 1)[0].strip()

        normalized["exchange"] = exchange

    website_url = normalized.get("website_url")
    if website_url:
        website_url = website_url.strip()

        # Remove simple Markdown-link wrapping if the model returned it.
        if website_url.startswith("[") and "](" in website_url:
            website_url = website_url.split("](", 1)[1].rstrip(")")

        normalized["website_url"] = website_url

    country_code = normalized.get("country_code")
    if country_code:
        normalized["country_code"] = country_code.strip().upper()

    return normalized

def validate_company_candidate(
    candidate: dict[str, Any],
) -> None:
    canonical_name = candidate.get("canonical_name")
    stock_ticker = candidate.get("stock_ticker")

    if not canonical_name:
        raise ValueError(
            "Verified company candidate has no canonical_name."
        )

    if not stock_ticker:
        raise ValueError(
            "Verified company candidate has no stock_ticker."
        )