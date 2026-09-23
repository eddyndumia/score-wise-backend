"""Shared load/save for period_metrics, used by score.py and statements.py —
factored out once two routers needed the identical dataclass<->jsonb mapping.
"""

import json
from dataclasses import asdict

from fastapi import HTTPException

from .scoring import FulizaMetrics, PeriodMetrics, RepaymentMetrics, SavingsMetrics


def _row_to_metrics(row: dict) -> PeriodMetrics:
    return PeriodMetrics(
        repayments=RepaymentMetrics(**row["repayments"]),
        fuliza=FulizaMetrics(**row["fuliza"]),
        savings=SavingsMetrics(**row["savings"]),
    )


async def load_period_metrics(conn, user_id: str) -> tuple[PeriodMetrics, PeriodMetrics] | None:
    """Returns (current, previous), or None if this borrower hasn't uploaded
    a statement yet. There are deliberately no defaults to fall back to: a
    score that didn't come from the borrower's own statement is a made-up
    number, and nobody (borrower or lender) should ever be shown one."""
    rows = await (await conn.execute(
        "select period, repayments, fuliza, savings from period_metrics where user_id = %s", (user_id,)
    )).fetchall()
    by_period = {r["period"]: _row_to_metrics(r) for r in rows}
    if "current" not in by_period or "previous" not in by_period:
        return None
    return by_period["current"], by_period["previous"]


NO_SCORE_DETAIL = {"code": "no_score", "message": "Upload your M-Pesa statement to get your score."}


async def require_period_metrics(conn, user_id: str) -> tuple[PeriodMetrics, PeriodMetrics]:
    """load_period_metrics for endpoints that can't do anything without a
    score — 404s with a stable code the apps turn into an "upload your
    statement" state instead of an error."""
    metrics = await load_period_metrics(conn, user_id)
    if metrics is None:
        raise HTTPException(status_code=404, detail=NO_SCORE_DETAIL)
    return metrics


async def save_period_metrics(conn, user_id: str, previous: PeriodMetrics, current: PeriodMetrics) -> None:
    for period, metrics in (("current", current), ("previous", previous)):
        d = asdict(metrics)
        await conn.execute(
            """
            insert into period_metrics (user_id, period, repayments, fuliza, savings, updated_at)
            values (%s, %s, %s, %s, %s, now())
            on conflict (user_id, period) do update set
              repayments = excluded.repayments, fuliza = excluded.fuliza, savings = excluded.savings, updated_at = now()
            """,
            (user_id, period, json.dumps(d["repayments"]), json.dumps(d["fuliza"]), json.dumps(d["savings"])),
        )
