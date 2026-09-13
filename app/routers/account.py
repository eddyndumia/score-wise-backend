import json
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response

from ..auth import AuthedUser, clear_auth_cookies, get_current_user
from ..db import db_conn
from ..metrics_repo import load_period_metrics
from ..seed import seed_new_account
from ..serializers import consent_to_json, grant_to_json

router = APIRouter()


@router.get("/v1/data-export")
async def export_data(user: AuthedUser = Depends(get_current_user)):
    """Self-service data export (Kenya's Data Protection Act 2019 right to
    portability, which the app's own Terms & Conditions already promises).
    Everything associated with the account, in one downloadable file — not a
    subset picked for convenience."""
    async with db_conn(user.id) as conn:
        profile_row = await (await conn.execute(
            "select account_name, profile_name from profiles where id = %s", (user.id,)
        )).fetchone()
        current, previous = await load_period_metrics(conn, user.id)
        cash_flow_row = await (await conn.execute("select series from cash_flow where user_id = %s", (user.id,))).fetchone()
        goal_row = await (await conn.execute(
            "select target_amount, created_at from savings_goals where user_id = %s", (user.id,)
        )).fetchone()
        grant_rows = await (await conn.execute(
            "select id, lender_name, expires_at from grants_table where user_id = %s", (user.id,)
        )).fetchall()
        pending_rows = await (await conn.execute(
            "select id, lender_name, grant_duration_days, will_share, wont_share from pending_consents where user_id = %s",
            (user.id,),
        )).fetchall()

    data = {
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "profile": {
            "profileName": profile_row["profile_name"] if profile_row else None,
            "accountName": profile_row["account_name"] if profile_row else None,
        },
        "score": {
            "currentPeriodMetrics": asdict(current),
            "previousPeriodMetrics": asdict(previous),
        },
        "cashFlow": cash_flow_row["series"] if cash_flow_row else [],
        "savingsGoal": {"targetAmount": float(goal_row["target_amount"]), "createdAt": goal_row["created_at"]} if goal_row else None,
        "lendersWithAccess": [
            grant_to_json({"id": str(r["id"]), "lender_name": r["lender_name"], "expires_at": r["expires_at"]}) for r in grant_rows
        ],
        "incomingLenderRequests": [
            consent_to_json({
                "id": str(r["id"]),
                "lender_name": r["lender_name"],
                "grant_duration_days": r["grant_duration_days"],
                "will_share": r["will_share"],
                "wont_share": r["wont_share"],
            })
            for r in pending_rows
        ],
    }
    body = json.dumps(data, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="pesascore-data-export.json"'},
    )


@router.delete("/v1/account")
async def delete_account(response: Response, user: AuthedUser = Depends(get_current_user)):
    """Soft reset — right to erasure for this account's data, but NOT the
    Supabase auth account itself (see the consumer app's CLAUDE.md for why:
    the same email/password should still log in afterward and start fresh,
    rather than this being a full, irreversible account deletion). Ends the
    current session too, since staying signed in past a data wipe makes no
    sense — the frontend lands back at Login."""
    async with db_conn(user.id) as conn:
        for table in ("period_metrics", "cash_flow", "pending_consents", "grants_table", "savings_goals", "notifications"):
            await conn.execute(f"delete from {table} where user_id = %s", (user.id,))
        await conn.execute(
            "update profiles set account_name = null, profile_name = null where id = %s", (user.id,)
        )
        await seed_new_account(conn, user.id)
    clear_auth_cookies(response)
    return {"ok": True}
