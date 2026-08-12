from __future__ import annotations

import json
import os
from typing import Any, Literal
from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from collectors.company_sync import normalize_alias_key, normalize_name


ALIAS_MODEL_ENV = "OPENAI_ALIAS_MODEL"
DEFAULT_ALIAS_MODEL = "gpt-5-mini"

AliasType = Literal[
    "SHORT_NAME",
    "TICKER",
    "FORMER_NAME",
    "PRODUCT",
    "BRAND",
    "SUBSIDIARY",
]


class AliasSuggestion(BaseModel):
    alias: str
    alias_type: AliasType
    confidence: float = Field(ge=0, le=1)
    source_urls: list[str] = Field(min_length=1, max_length=5)
    evidence_summary: str


class CompanyAliasResearch(BaseModel):
    company_verified: bool
    company_summary: str
    aliases: list[AliasSuggestion] = Field(max_length=20)


def create_openai_client() -> OpenAI:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    return OpenAI(api_key=api_key)


def get_alias_model() -> str:
    load_dotenv()
    return os.getenv(ALIAS_MODEL_ENV, DEFAULT_ALIAS_MODEL)


def build_alias_research_input(
    company: dict[str, Any],
    existing_aliases: list[dict[str, Any]],
) -> str:
    payload = {
        "company_id": company.get("company_id"),
        "canonical_name": company.get("canonical_name"),
        "primary_ticker": company.get("primary_ticker"),
        "exchange": company.get("exchange"),
        "country_code": company.get("country_code"),
        "website_url": company.get("website_url"),
        "existing_aliases": [
            {
                "alias": row.get("alias"),
                "alias_type": row.get("alias_type"),
            }
            for row in existing_aliases
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def research_company_aliases(
    company: dict[str, Any],
    existing_aliases: list[dict[str, Any]],
    *,
    client: Any | None = None,
    model: str | None = None,
) -> CompanyAliasResearch:
    """Research sourced alias candidates for one known company."""

    openai_client = client or create_openai_client()
    selected_model = model or get_alias_model()
    company_input = build_alias_research_input(company, existing_aliases)

    response = openai_client.responses.parse(
        model=selected_model,
        tools=[{"type": "web_search"}],
        input=[
            {
                "role": "system",
                "content": (
                    "Research aliases for exactly one existing company. Use web "
                    "search and reliable public sources, prioritizing the company's "
                    "official website, exchange filings, regulator filings, and "
                    "reputable reporting. Suggest only names that clearly identify "
                    "this company or its owned brands, products, and subsidiaries. "
                    "Do not suggest generic industry terms, slogans, executive names, "
                    "competitors, customers, or merely related organizations. A brand, "
                    "product, or subsidiary must have evidence of ownership. A former "
                    "name must have evidence of the rename or corporate lineage. "
                    "Return the direct evidence page URL for every suggestion. Do not "
                    "repeat an existing alias. If the supplied company identity cannot "
                    "be verified, set company_verified=false and return no aliases."
                ),
            },
            {
                "role": "user",
                "content": company_input,
            },
        ],
        text_format=CompanyAliasResearch,
    )

    result = response.output_parsed
    if result is None:
        raise RuntimeError("OpenAI returned no structured alias research result.")

    return result


def is_public_evidence_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except (TypeError, ValueError):
        return False

    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
    )


def build_pending_alias_rows(
    company_id: int,
    research: CompanyAliasResearch,
    existing_aliases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert valid, new suggestions into inactive database rows."""

    if not research.company_verified:
        return []

    seen_keys = {
        normalize_alias_key(row.get("alias"))
        for row in existing_aliases
        if row.get("alias")
    }
    rows: list[dict[str, Any]] = []

    for suggestion in research.aliases:
        alias = normalize_name(suggestion.alias)
        alias_key = normalize_alias_key(alias)
        source_urls = list(
            dict.fromkeys(url.strip() for url in suggestion.source_urls)
        )

        if not alias or not alias_key or alias_key in seen_keys:
            continue
        if not all(is_public_evidence_url(url) for url in source_urls):
            continue

        seen_keys.add(alias_key)
        rows.append(
            {
                "company_id": company_id,
                "alias": alias,
                "alias_type": suggestion.alias_type,
                "is_active": False,
                "confidence": suggestion.confidence,
                "verification_status": "PENDING",
                "source_urls": source_urls,
                "generated_by": "AI",
                "notes": normalize_name(suggestion.evidence_summary),
            }
        )

    return rows


def save_alias_rows(
    supabase: Any,
    rows: list[dict[str, Any]],
) -> int:
    if not rows:
        return 0

    response = supabase.table("company_aliases").insert(rows).execute()
    return len(response.data or [])
