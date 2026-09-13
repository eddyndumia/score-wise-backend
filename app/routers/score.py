from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from ..auth import AuthedUser, get_current_user
from ..db import db_conn
from ..metrics_repo import load_period_metrics
from ..report import generate_score_report_pdf
from ..scoring import apply_hypothetical, compute_score
from ..serializers import score_result_to_json

router = APIRouter()


@router.get("/v1/score")
async def get_score(user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        current, previous = await load_period_metrics(conn, user.id)
    result = compute_score(current, previous)
    return score_result_to_json(result)


@router.get("/v1/score/report")
async def get_score_report(user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        current, previous = await load_period_metrics(conn, user.id)
        profile_row = await (await conn.execute("select profile_name from profiles where id = %s", (user.id,))).fetchone()
    result = compute_score(current, previous)
    profile_name = profile_row["profile_name"] if profile_row else None
    pdf_bytes = generate_score_report_pdf(profile_name, result)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="pesascore-score-report.pdf"'},
    )


@router.get("/v1/score/simulate/limits")
async def get_simulate_limits(user: AuthedUser = Depends(get_current_user)):
    """Bounds for the simulator's sliders, derived from the real current
    metrics — e.g. you can't simulate cutting more Fuliza days than you
    actually used."""
    async with db_conn(user.id) as conn:
        current, _ = await load_period_metrics(conn, user.id)
    return {
        "fulizaDaysActive": current.fuliza.days_active,
        "latePlusMissed": current.repayments.late + current.repayments.missed,
        "totalIncome": current.savings.total_income,
    }


class SimulateRequest(BaseModel):
    extraSavings: float = 0
    fulizaReductionDays: int = 0
    extraOnTimePayments: int = 0


@router.post("/v1/score/simulate")
async def simulate_score(body: SimulateRequest, user: AuthedUser = Depends(get_current_user)):
    async with db_conn(user.id) as conn:
        current, _ = await load_period_metrics(conn, user.id)
    hypothetical = apply_hypothetical(
        current,
        extra_savings=body.extraSavings,
        fuliza_reduction_days=body.fulizaReductionDays,
        extra_on_time_payments=body.extraOnTimePayments,
    )
    # Compared against the real current score, not the real previous one —
    # the useful question here is "vs. where I actually am today", not "vs.
    # last period".
    result = compute_score(hypothetical, current)
    return score_result_to_json(result)
