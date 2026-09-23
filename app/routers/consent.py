import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..access_log import log_event
from ..auth import AuthedUser, get_current_user
from ..db import db_conn
from ..serializers import consent_to_json, grant_to_json
from ..metrics_repo import load_period_metrics
from ..store import DAY_MS

router = APIRouter()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _consent_row_to_dict(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "lender_name": row["lender_name"],
        "grant_duration_days": row["grant_duration_days"],
        "will_share": row["will_share"],
        "wont_share": row["wont_share"],
    }


def _grant_row_to_dict(row: dict) -> dict:
    return {"id": str(row["id"]), "lender_name": row["lender_name"], "expires_at": row["expires_at"]}


@router.get("/v1/consent")
async def list_pending_consent(user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        rows = await (await conn.execute(
            "select id, lender_name, grant_duration_days, will_share, wont_share from pending_consents"
            " where user_id = %s and status = 'pending' order by created_at",
            (user.id,),
        )).fetchall()
    return [consent_to_json(_consent_row_to_dict(r)) for r in rows]


@router.get("/v1/consent/{request_id}")
async def get_pending_consent(request_id: str, user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        row = await (await conn.execute(
            "select id, lender_name, grant_duration_days, will_share, wont_share from pending_consents"
            " where user_id = %s and id = %s and status = 'pending'",
            (user.id, request_id),
        )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return consent_to_json(_consent_row_to_dict(row))


class ConsentResponse(BaseModel):
    approve: bool


@router.post("/v1/consent/{request_id}/respond")
async def respond_to_consent(request_id: str, body: ConsentResponse, user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        # Marked resolved (approved/denied) instead of deleted, so a lender
        # can later see what happened to a request it sent — see
        # routers/lender.py's GET /v1/lender/consent-requests. 404 covers
        # both "never existed" and "already resolved" the same way a delete
        # used to (no oracle either way).
        row = await (await conn.execute(
            "select lender_name, lender_id, grant_duration_days, will_share from pending_consents"
            " where user_id = %s and id = %s and status = 'pending'",
            (user.id, request_id),
        )).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Request not found")

        if not body.approve:
            await conn.execute("update pending_consents set status = 'denied' where id = %s", (request_id,))
            await log_event(conn, borrower_id=user.id, lender_id=row["lender_id"], lender_name=row["lender_name"],
                            event="request_denied", detail={"requestId": request_id})
            return {"ok": True, "grant": None}

        # The borrower sees their score before anyone else does, so there's
        # nothing to share until a statement has been uploaded. The request
        # stays pending; the app sends them to upload first.
        if await load_period_metrics(conn, user.id) is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "no_score", "message": "Upload your M-Pesa statement first, so you can see your score before you share it."},
            )

        await conn.execute("update pending_consents set status = 'approved' where id = %s", (request_id,))

        # lender_id/will_share carry through so real per-grant enforcement
        # (routers/lender.py) survives past approval — a request with no
        # lender_id (only pre-existing rows) produces a grant no lender endpoint
        # can ever match, exactly as before this pass.
        expires_at = _now_ms() + row["grant_duration_days"] * DAY_MS
        grant_row = await (await conn.execute(
            "insert into grants_table (user_id, lender_id, lender_name, expires_at, will_share)"
            " values (%s, %s, %s, %s, %s) returning id, lender_name, expires_at",
            (user.id, row["lender_id"], row["lender_name"], expires_at, json.dumps(row["will_share"] or [])),
        )).fetchone()
        await log_event(conn, borrower_id=user.id, lender_id=row["lender_id"], lender_name=row["lender_name"],
                        event="request_approved",
                        detail={"requestId": request_id, "grantId": str(grant_row["id"]), "shares": row["will_share"] or [],
                                "days": row["grant_duration_days"]})
    return {"ok": True, "grant": grant_to_json(_grant_row_to_dict(grant_row))}

