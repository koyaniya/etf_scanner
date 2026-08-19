from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal


ReviewAction = Literal["approve", "reject", "deactivate", "reopen"]


ALLOWED_TRANSITIONS = {
    "approve": {("PENDING", False)},
    "reject": {("PENDING", False), ("VERIFIED", True), ("VERIFIED", False)},
    "deactivate": {("VERIFIED", True)},
    "reopen": {("REJECTED", False), ("VERIFIED", False)},
}


def append_review_note(
    existing_notes: str | None,
    *,
    action: ReviewAction,
    reviewed_by: str,
    reviewed_at: str,
    note: str | None,
) -> str:
    audit_entry = f"[{reviewed_at}] {action.upper()} by {reviewed_by}."
    if note and note.strip():
        audit_entry = f"{audit_entry} {note.strip()}"

    if existing_notes and existing_notes.strip():
        return f"{existing_notes.rstrip()}\n{audit_entry}"

    return audit_entry


def build_review_update(
    alias: dict[str, Any],
    action: ReviewAction,
    reviewed_by: str,
    *,
    note: str | None = None,
    reviewed_at: datetime | None = None,
) -> dict[str, Any]:
    reviewer = reviewed_by.strip()
    if not reviewer:
        raise ValueError("reviewed_by must not be blank.")

    current_state = (
        alias.get("verification_status"),
        bool(alias.get("is_active")),
    )
    if current_state not in ALLOWED_TRANSITIONS[action]:
        raise ValueError(
            f"Cannot {action} alias from state "
            f"{current_state[0]}/{current_state[1]}."
        )

    timestamp = reviewed_at or datetime.now(timezone.utc)
    timestamp_text = timestamp.astimezone(timezone.utc).isoformat()

    if action == "approve":
        verification_status = "VERIFIED"
        is_active = True
    elif action == "reject":
        verification_status = "REJECTED"
        is_active = False
    elif action == "deactivate":
        verification_status = "VERIFIED"
        is_active = False
    else:
        verification_status = "PENDING"
        is_active = False

    return {
        "verification_status": verification_status,
        "is_active": is_active,
        "reviewed_at": timestamp_text,
        "reviewed_by": reviewer,
        "notes": append_review_note(
            alias.get("notes"),
            action=action,
            reviewed_by=reviewer,
            reviewed_at=timestamp_text,
            note=note,
        ),
    }


def load_alias(supabase: Any, alias_id: int) -> dict[str, Any]:
    response = (
        supabase.table("company_aliases")
        .select(
            "alias_id,company_id,alias,alias_type,is_active,confidence,"
            "verification_status,source_urls,generated_by,notes,"
            "reviewed_at,reviewed_by,created_at,companies(canonical_name)"
        )
        .eq("alias_id", alias_id)
        .limit(1)
        .execute()
    )

    if not response.data:
        raise RuntimeError(f"Alias {alias_id} was not found.")

    return response.data[0]


def list_aliases_for_review(
    supabase: Any,
    *,
    status: str = "PENDING",
    company_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    query = (
        supabase.table("company_aliases")
        .select(
            "alias_id,company_id,alias,alias_type,is_active,confidence,"
            "verification_status,source_urls,generated_by,notes,created_at,"
            "companies(canonical_name)"
        )
        .eq("verification_status", status)
        .order("created_at")
        .limit(limit)
    )

    if company_id is not None:
        query = query.eq("company_id", company_id)

    response = query.execute()
    return response.data or []


def apply_review_action(
    supabase: Any,
    alias_id: int,
    action: ReviewAction,
    reviewed_by: str,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    alias = load_alias(supabase, alias_id)
    update = build_review_update(
        alias,
        action,
        reviewed_by,
        note=note,
    )

    # Include the state read above in the update filter. If another reviewer acts
    # first, this update returns no rows rather than overwriting their decision.
    response = (
        supabase.table("company_aliases")
        .update(update)
        .eq("alias_id", alias_id)
        .eq("verification_status", alias["verification_status"])
        .eq("is_active", alias["is_active"])
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Alias state changed during review; reload it before trying again."
        )

    return response.data[0]
