import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import AuthedUser, get_current_user
from ..db import db_conn

router = APIRouter()


class SavingsGoalUpdate(BaseModel):
    targetAmount: float = Field(gt=0)


@router.get("/v1/savings-goal")
async def get_savings_goal(user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        metrics_row = await (await conn.execute(
            "select savings from period_metrics where user_id = %s and period = 'current'", (user.id,)
        )).fetchone()
        goal_row = await (await conn.execute(
            "select target_amount, created_at from savings_goals where user_id = %s", (user.id,)
        )).fetchone()

    saved_so_far = metrics_row["savings"]["total_saved"] if metrics_row else 0
    if goal_row is None:
        return {"goal": None, "savedSoFar": round(saved_so_far, 2)}
    return {
        "goal": {"targetAmount": float(goal_row["target_amount"]), "createdAt": goal_row["created_at"]},
        "savedSoFar": round(saved_so_far, 2),
    }


@router.put("/v1/savings-goal")
async def set_savings_goal(body: SavingsGoalUpdate, user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        await conn.execute(
            """
            insert into savings_goals (user_id, target_amount, created_at)
            values (%s, %s, %s)
            on conflict (user_id) do update set target_amount = excluded.target_amount, created_at = excluded.created_at
            """,
            (user.id, body.targetAmount, int(time.time() * 1000)),
        )
    return {"ok": True}


@router.delete("/v1/savings-goal")
async def clear_savings_goal(user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        result = await conn.execute("delete from savings_goals where user_id = %s", (user.id,))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="No savings goal set")
    return {"ok": True}
