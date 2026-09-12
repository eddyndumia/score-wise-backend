from fastapi import APIRouter, Depends

from ..auth import AuthedUser, get_current_user
from ..db import db_conn

router = APIRouter()


@router.get("/v1/cash-flow")
async def get_cash_flow(user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        row = await (await conn.execute("select series from cash_flow where user_id = %s", (user.id,))).fetchone()
    return {"series": row["series"] if row else []}
