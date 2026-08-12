from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from agents.company_alias_agent import (
    CompanyAliasResearch,
    get_alias_model,
    research_company_aliases,
    save_alias_rows,
)
from processing.alias_validator import (
    database_rows_from_validation,
    validate_alias_suggestions,
)


RUNS_TABLE = "company_alias_enrichment_runs"
DEFAULT_REFRESH_DAYS = 180


def get_alias_research_refresh_days() -> int:
    value = os.getenv(
        "ALIAS_RESEARCH_REFRESH_DAYS",
        str(DEFAULT_REFRESH_DAYS),
    )
    try:
        refresh_days = int(value)
    except ValueError as exc:
        raise ValueError(
            "ALIAS_RESEARCH_REFRESH_DAYS must be an integer."
        ) from exc

    if refresh_days < 1:
        raise ValueError("ALIAS_RESEARCH_REFRESH_DAYS must be at least 1.")
    return refresh_days


def load_companies_for_alias_enrichment(
    supabase: Any,
    *,
    company_id: int | None = None,
) -> list[dict[str, Any]]:
    query = supabase.table("companies").select(
        "company_id,canonical_name,primary_ticker,exchange,country_code,"
        "website_url,is_active"
    )
    if company_id is not None:
        query = query.eq("company_id", company_id)
    else:
        query = query.eq("is_active", True)

    response = query.order("company_id").execute()
    return response.data or []


def load_aliases(supabase: Any) -> list[dict[str, Any]]:
    page_size = 1000
    start = 0
    aliases: list[dict[str, Any]] = []

    while True:
        response = (
            supabase.table("company_aliases")
            .select(
                "company_id,alias,alias_type,verification_status,is_active"
            )
            .range(start, start + page_size - 1)
            .execute()
        )
        page = response.data or []
        aliases.extend(page)
        if len(page) < page_size:
            return aliases
        start += page_size


def load_latest_successful_research_dates(
    supabase: Any,
) -> dict[int, datetime]:
    response = (
        supabase.table(RUNS_TABLE)
        .select("company_id,completed_at,started_at")
        .eq("status", "SUCCESS")
        .order("started_at", desc=True)
        .execute()
    )
    latest: dict[int, datetime] = {}

    for row in response.data or []:
        company_id = row["company_id"]
        if company_id in latest:
            continue
        timestamp = row.get("completed_at") or row.get("started_at")
        if not timestamp:
            continue
        latest[company_id] = datetime.fromisoformat(
            timestamp.replace("Z", "+00:00")
        )

    return latest


def select_companies_for_enrichment(
    companies: list[dict[str, Any]],
    latest_success_dates: dict[int, datetime],
    *,
    limit: int,
    refresh_days: int,
    force: bool = False,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    if refresh_days < 1:
        raise ValueError("refresh_days must be at least 1.")

    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(days=refresh_days)

    eligible = [
        company
        for company in companies
        if force
        or company["company_id"] not in latest_success_dates
        or latest_success_dates[company["company_id"]] <= cutoff
    ]
    return eligible[:limit]


def retry_research(
    research: Callable[[], CompanyAliasResearch],
    *,
    max_attempts: int,
    retry_delay_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[CompanyAliasResearch, int]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1.")

    for attempt in range(1, max_attempts + 1):
        try:
            return research(), attempt
        except Exception:
            if attempt == max_attempts:
                raise
            sleep(retry_delay_seconds * attempt)

    raise AssertionError("unreachable")


def insert_run(supabase: Any, company_id: int, model: str) -> int:
    response = (
        supabase.table(RUNS_TABLE)
        .insert(
            {
                "company_id": company_id,
                "status": "RUNNING",
                "model": model,
            }
        )
        .execute()
    )
    if not response.data:
        raise RuntimeError("Could not create alias enrichment run record.")
    return response.data[0]["run_id"]


def finish_run(
    supabase: Any,
    run_id: int,
    *,
    status: str,
    attempt_count: int,
    company_verified: bool | None = None,
    suggestions_received: int = 0,
    aliases_saved: int = 0,
    error_message: str | None = None,
) -> None:
    (
        supabase.table(RUNS_TABLE)
        .update(
            {
                "status": status,
                "attempt_count": attempt_count,
                "company_verified": company_verified,
                "suggestions_received": suggestions_received,
                "aliases_saved": aliases_saved,
                "error_message": error_message,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("run_id", run_id)
        .execute()
    )


def enrich_company(
    supabase: Any,
    company: dict[str, Any],
    all_aliases: list[dict[str, Any]],
    *,
    save: bool,
    model: str,
    max_attempts: int,
    retry_delay_seconds: float,
    research_function: Callable[..., CompanyAliasResearch] = (
        research_company_aliases
    ),
) -> dict[str, Any]:
    company_id = company["company_id"]
    existing_aliases = [
        row for row in all_aliases if row["company_id"] == company_id
    ]

    research, attempts = retry_research(
        lambda: research_function(
            company,
            existing_aliases,
            model=model,
        ),
        max_attempts=max_attempts,
        retry_delay_seconds=retry_delay_seconds,
    )
    validation = validate_alias_suggestions(
        company,
        research,
        existing_aliases,
        all_aliases,
    )
    rows = database_rows_from_validation(validation)
    saved = save_alias_rows(supabase, rows) if save else 0
    # Make candidates from this company visible to ambiguity checks for later
    # companies in the same batch, including in preview mode.
    all_aliases.extend(rows)

    return {
        "company_id": company_id,
        "canonical_name": company.get("canonical_name"),
        "attempt_count": attempts,
        "company_verified": research.company_verified,
        "suggestions_received": len(research.aliases),
        "aliases_to_save": len(rows),
        "aliases_saved": saved,
        "validation_results": [result.as_dict() for result in validation],
    }


def run_alias_enrichment_batch(
    supabase: Any,
    *,
    limit: int,
    save: bool = False,
    force: bool = False,
    company_id: int | None = None,
    model: str | None = None,
    refresh_days: int | None = None,
    max_attempts: int = 2,
    retry_delay_seconds: float = 2,
    research_function: Callable[..., CompanyAliasResearch] = (
        research_company_aliases
    ),
) -> dict[str, Any]:
    selected_model = model or get_alias_model()
    selected_refresh_days = (
        refresh_days
        if refresh_days is not None
        else get_alias_research_refresh_days()
    )
    companies = load_companies_for_alias_enrichment(
        supabase,
        company_id=company_id,
    )
    latest_success_dates = load_latest_successful_research_dates(supabase)
    selected = select_companies_for_enrichment(
        companies,
        latest_success_dates,
        limit=limit,
        refresh_days=selected_refresh_days,
        force=force,
    )
    all_aliases = load_aliases(supabase)
    results: list[dict[str, Any]] = []

    for company in selected:
        run_id: int | None = None

        try:
            if save:
                run_id = insert_run(
                    supabase,
                    company["company_id"],
                    selected_model,
                )
            result = enrich_company(
                supabase,
                company,
                all_aliases,
                save=save,
                model=selected_model,
                max_attempts=max_attempts,
                retry_delay_seconds=retry_delay_seconds,
                research_function=research_function,
            )
            result["status"] = "SUCCESS"
            results.append(result)

            if save and run_id is not None:
                finish_run(
                    supabase,
                    run_id,
                    status="SUCCESS",
                    attempt_count=result["attempt_count"],
                    company_verified=result["company_verified"],
                    suggestions_received=result["suggestions_received"],
                    aliases_saved=result["aliases_saved"],
                )
        except Exception as exc:
            result = {
                "company_id": company["company_id"],
                "canonical_name": company.get("canonical_name"),
                "status": "FAILED",
                "error": str(exc),
            }
            results.append(result)
            if save and run_id is not None:
                finish_run(
                    supabase,
                    run_id,
                    status="FAILED",
                    attempt_count=max_attempts,
                    error_message=str(exc)[:2000],
                )

    return {
        "mode": "SAVE" if save else "PREVIEW",
        "model": selected_model,
        "refresh_days": selected_refresh_days,
        "selected_companies": len(selected),
        "succeeded": sum(row["status"] == "SUCCESS" for row in results),
        "failed": sum(row["status"] == "FAILED" for row in results),
        "results": results,
    }
