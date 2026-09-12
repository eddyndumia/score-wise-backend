from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import AuthedUser, get_current_user
from ..db import db_conn

router = APIRouter()


@router.get("/v1/profile")
async def get_profile(user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        row = await (await conn.execute("select profile_name from profiles where id = %s", (user.id,))).fetchone()
    return {"name": row["profile_name"] if row else None}


class ProfileUpdate(BaseModel):
    name: str


@router.put("/v1/profile")
async def update_profile(body: ProfileUpdate, user: AuthedUser = Depends(get_current_user)):
    trimmed = body.name.strip()
    async with db_conn(user.id) as conn:
        if trimmed:
            await conn.execute("update profiles set profile_name = %s where id = %s", (trimmed, user.id))
        row = await (await conn.execute("select profile_name from profiles where id = %s", (user.id,))).fetchone()
    return {"name": row["profile_name"] if row else None}
