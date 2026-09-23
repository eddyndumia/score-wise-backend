from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import AuthedUser, get_current_user
from ..db import db_conn

router = APIRouter()


class PushToken(BaseModel):
    token: str = Field(min_length=10, max_length=4096)
    platform: Literal["android", "ios", "web"] = "android"


@router.post("/v1/push-token")
async def register_push_token(body: PushToken, user: AuthedUser = Depends(get_current_user)):
    """The phone's FCM token, sent after sign-in/unlock and whenever Firebase
    rotates it. Moves the token to this account if another account on the
    same phone had it (see supabase/migrations/20260923_push_tokens.sql)."""
    async with db_conn(user.id) as conn:
        await conn.execute("select register_push_token(%s, %s)", (body.token, body.platform))
    return {"ok": True}


class TokenOnly(BaseModel):
    token: str


@router.post("/v1/push-token/remove")
async def remove_push_token(body: TokenOnly, user: AuthedUser = Depends(get_current_user)):
    """On sign-out, so a phone stops getting pushes for an account that left it."""
    async with db_conn(user.id) as conn:
        await conn.execute("delete from push_tokens where user_id = %s and token = %s", (user.id, body.token))
    return {"ok": True}
