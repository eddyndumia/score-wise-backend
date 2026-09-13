import json
import random
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import AuthedUser, get_current_user
from ..consent_categories import STANDARD_WILL_SHARE
from ..db import db_conn
from ..serializers import consent_to_json, grant_to_json
from ..store import DAY_MS, SIMULATED_LENDER_POOL

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
            "select id, lender_name, grant_duration_days, will_share, wont_share from pending_consents where user_id = %s order by created_at",
            (user.id,),
        )).fetchall()
    return [consent_to_json(_consent_row_to_dict(r)) for r in rows]


@router.get("/v1/consent/{request_id}")
async def get_pending_consent(request_id: str, user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        row = await (await conn.execute(
            "select id, lender_name, grant_duration_days, will_share, wont_share from pending_consents where user_id = %s and id = %s",
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
        row = await (await conn.execute(
            "delete from pending_consents where user_id = %s and id = %s"
            " returning lender_name, lender_id, grant_duration_days, will_share",
            (user.id, request_id),
        )).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Request not found")

        if not body.approve:
            return {"ok": True, "grant": None}

        # lender_id/will_share carry through so real per-grant enforcement
        # (routers/lender.py) survives past approval — a demo/simulated
        # request (lender_id null) produces a grant no real lender endpoint
        # can ever match, exactly as before this pass.
        expires_at = _now_ms() + row["grant_duration_days"] * DAY_MS
        grant_row = await (await conn.execute(
            "insert into grants_table (user_id, lender_id, lender_name, expires_at, will_share)"
            " values (%s, %s, %s, %s, %s) returning id, lender_name, expires_at",
            (user.id, row["lender_id"], row["lender_name"], expires_at, json.dumps(row["will_share"] or [])),
        )).fetchone()
    return {"ok": True, "grant": grant_to_json(_grant_row_to_dict(grant_row))}


@router.post("/v1/consent/simulate")
async def simulate_incoming_request(user: AuthedUser = Depends(get_current_user)):
    will_share = STANDARD_WILL_SHARE
    wont_share = ["Full transaction amounts", "Contact list", "Balances on other accounts"]

    async with db_conn(user.id) as conn:
        pending = await (await conn.execute("select lender_name from pending_consents where user_id = %s", (user.id,))).fetchall()
        granted = await (await conn.execute("select lender_name from grants_table where user_id = %s", (user.id,))).fetchall()
        pending_names = {r["lender_name"] for r in pending}
        granted_names = {r["lender_name"] for r in granted}
        available = [name for name in SIMULATED_LENDER_POOL if name not in pending_names and name not in granted_names]
        lender_name = random.choice(available) if available else random.choice(SIMULATED_LENDER_POOL)

        row = await (await conn.execute(
            """
            insert into pending_consents (user_id, lender_name, grant_duration_days, will_share, wont_share)
            values (%s, %s, 14, %s, %s)
            returning id, lender_name, grant_duration_days, will_share, wont_share
            """,
            (user.id, lender_name, json.dumps(will_share), json.dumps(wont_share)),
        )).fetchone()

        await conn.execute(
            "insert into notifications (user_id, kind, message, created_at) values (%s, %s, %s, %s)",
            (user.id, "consent_request", f"{lender_name} wants access to your credit profile.", _now_ms()),
        )
    return consent_to_json(_consent_row_to_dict(row))
