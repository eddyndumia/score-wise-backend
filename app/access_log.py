"""Append-only consent/access audit log (supabase/migrations/20260923_access_log.sql).

Written inside the caller's own RLS-scoped transaction, so the database
itself decides whether the caller may record that event: a borrower can only
log decisions about themselves, a lender only requests it really sent and
views of borrowers it holds an unexpired grant for. The app can't edit or
delete a row once written.
"""

import json

BORROWER_EVENTS = {"request_approved", "request_denied", "access_revoked"}
LENDER_EVENTS = {"request_sent", "score_viewed"}


async def log_event(
    conn,
    *,
    borrower_id: str,
    lender_id: str | None,
    lender_name: str,
    event: str,
    detail: dict | None = None,
) -> None:
    assert event in BORROWER_EVENTS | LENDER_EVENTS, event
    await conn.execute(
        "insert into access_log (borrower_id, lender_id, lender_name, event, detail) values (%s, %s, %s, %s, %s)",
        (borrower_id, lender_id, lender_name, event, json.dumps(detail or {})),
    )


def entry_to_json(row: dict) -> dict:
    return {
        "id": row["id"],
        "at": row["created_at"].isoformat(),
        "lenderName": row["lender_name"],
        "event": row["event"],
        "detail": row["detail"] or {},
    }
