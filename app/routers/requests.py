from fastapi import APIRouter, Depends, HTTPException

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
        result = await conn.execute("delete from grants_table where user_id = %s and id = %s", (user.id, grant_id))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Grant not found")
    return {"ok": True}
