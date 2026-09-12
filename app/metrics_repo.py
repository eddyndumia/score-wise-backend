"""Shared load/save for period_metrics, used by score.py and statements.py —
factored out once two routers needed the identical dataclass<->jsonb mapping.
"""

import json
from dataclasses import asdict

from .scoring import FulizaMetrics, PeriodMetrics, RepaymentMetrics, SavingsMetrics
from .store import DEFAULT_CURRENT_METRICS, DEFAULT_PREVIOUS_METRICS


def _row_to_metrics(row: dict) -> PeriodMetrics:
    return PeriodMetrics(
        repayments=RepaymentMetrics(**row["repayments"]),
        fuliza=FulizaMetrics(**row["fuliza"]),
        savings=SavingsMetrics(**row["savings"]),
    )


async def load_period_metrics(conn, user_id: str) -> tuple[PeriodMetrics, PeriodMetrics]:
    """Returns (current, previous). Falls back to the same defaults a fresh
    account is seeded with if a row is somehow missing (shouldn't happen post
    signup, but avoids a hard failure if it does)."""
    rows = await (await conn.execute(
        "select period, repayments, fuliza, savings from period_metrics where user_id = %s", (user_id,)
    )).fetchall()
    by_period = {r["period"]: _row_to_metrics(r) for r in rows}
    current = by_period.get("current", DEFAULT_CURRENT_METRICS)
    previous = by_period.get("previous", DEFAULT_PREVIOUS_METRICS)
    return current, previous


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
