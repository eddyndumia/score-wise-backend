from fastapi import APIRouter, Depends, HTTPException

from ..auth import AuthedUser, get_current_user
from ..db import db_conn
from ..serializers import notification_to_json

router = APIRouter()


def _row_to_dict(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "message": row["message"],
        "created_at": row["created_at"],
        "read": row["read"],
    }


@router.get("/v1/notifications")
async def list_notifications(user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        rows = await (await conn.execute(
            "select id, kind, message, created_at, read from notifications where user_id = %s order by created_at desc",
            (user.id,),
        )).fetchall()
    return [notification_to_json(_row_to_dict(r)) for r in rows]


@router.post("/v1/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        result = await conn.execute(
            "update notifications set read = true where user_id = %s and id = %s", (user.id, notification_id)
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"ok": True}


@router.post("/v1/notifications/read-all")
async def mark_all_notifications_read(user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        await conn.execute("update notifications set read = true where user_id = %s", (user.id,))
    return {"ok": True}
