from fastapi import APIRouter, Depends, HTTPException

from ..access_log import log_event
from ..auth import AuthedUser, get_current_user
from ..db import db_conn
from ..serializers import grant_to_json

router = APIRouter()


@router.get("/v1/requests")
async def get_active_grants(user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        rows = await (await conn.execute(
            "select id, lender_name, expires_at from grants_table where user_id = %s order by created_at",
            (user.id,),
        )).fetchall()
    return [grant_to_json({"id": str(r["id"]), "lender_name": r["lender_name"], "expires_at": r["expires_at"]}) for r in rows]


@router.post("/v1/requests/{grant_id}/revoke")
async def revoke_grant(grant_id: str, user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        gone = await (await conn.execute(
            "delete from grants_table where user_id = %s and id = %s returning lender_id, lender_name",
            (user.id, grant_id),
        )).fetchone()
        if gone is None:
            raise HTTPException(status_code=404, detail="Grant not found")
        # The grant row is gone, so this is the only record that access existed and ended.
        await log_event(conn, borrower_id=user.id, lender_id=gone["lender_id"], lender_name=gone["lender_name"],
                        event="access_revoked", detail={"grantId": grant_id})
    return {"ok": True}
