"""Ported from scorewise-consumer/frontend/src/lib/scoring.ts — keep the two in sync.
The frontend's copy is now dead code once api/score.ts calls this backend, but is left
in place as a reference / offline fallback.
"""

from dataclasses import dataclass, field
from typing import Literal

SignalStatus = Literal["strong", "moderate", "risk"]

MIN_SCORE = 300
MAX_SCORE = 850


@dataclass
class RepaymentMetrics:
    on_time: int
    late: int
    missed: int


@dataclass
class FulizaMetrics:
    days_active: int
    period_days: int


@dataclass
class SavingsMetrics:
    total_saved: float
    total_income: float


@dataclass
class PeriodMetrics:
    repayments: RepaymentMetrics
    fuliza: FulizaMetrics
    savings: SavingsMetrics


@dataclass
class Signal:
    label: str
    value: int
    status: SignalStatus
    explanation: str
    recommendation: str = ""


@dataclass
class ScoreResult:
    score: int
    previous_score: int
    delta: int
    signals: list[Signal] = field(default_factory=list)
    tip: str = ""


# Rule-based, not AI — the three signals only ever land in one of nine
# (label, status) combinations, so a lookup table is simpler, free, instant,
# and fully deterministic/testable. An LLM would add cost, latency, and
# non-determinism for a problem this small; only worth revisiting if
# recommendations need to reference specifics an LLM could phrase better than
# a template can (e.g. naming the actual lender), not for the current 1-of-9 case.
_REPAYMENT_RECOMMENDATIONS: dict[SignalStatus, str] = {
    "strong": "Keep it up — consistent, on-time payments are the single biggest factor in your score.",
    "moderate": "A few late or missed payments are holding this back. Setting reminders (or autopay, if your lender supports it) for upcoming due dates could help.",
    "risk": "Frequent missed payments are seriously hurting your score. Catching up on any overdue loan or SACCO payments is the highest-impact thing you can do here.",
}
_FULIZA_RECOMMENDATIONS: dict[SignalStatus, str] = {
    "strong": "You rarely rely on overdraft — that's a strong signal of day-to-day financial stability.",
    "moderate": "Occasional Fuliza use isn't unusual, but cutting back further would strengthen this signal.",
    "risk": "Heavy Fuliza reliance usually points to cash-flow strain. Reducing top-ups over the next few months is likely the fastest way to raise your score.",
}
_SAVINGS_RECOMMENDATIONS: dict[SignalStatus, str] = {
    "strong": "Your consistent savings relative to income is a strong positive signal — keep the pattern going.",
    "moderate": "Increasing regular deposits, even small ones, would strengthen this signal over time.",
    "risk": "Very little is currently being set aside. Even small, regular savings deposits — weekly rather than one lump sum — tend to move this signal fastest.",
}


def _status_for(value: float, good_above: float, risk_below: float) -> SignalStatus:
    if value >= good_above:
        return "strong"
    if value <= risk_below:
        return "risk"
    return "moderate"


def _score_repayment_history(m: RepaymentMetrics) -> Signal:
    total = m.on_time + m.late + m.missed
    value = 100 if total == 0 else round((m.on_time / total) * 100)
    status = _status_for(value, 80, 50)
    if status == "strong":
        explanation = "Consistent, on-time payments over the last 6 months."
    elif status == "moderate":
        explanation = f"A few late or missed payments ({m.late + m.missed} of {total}) in the last 6 months."
    else:
        explanation = f"Frequent late or missed payments ({m.late + m.missed} of {total}) in the last 6 months."
    return Signal("Repayment history", value, status, explanation, _REPAYMENT_RECOMMENDATIONS[status])


def _score_fuliza_reliance(m: FulizaMetrics) -> Signal:
    reliance = 0 if m.period_days == 0 else round((m.days_active / m.period_days) * 100)
    # Higher reliance is worse, so thresholds are inverted vs. the other signals.
    status: SignalStatus = "strong" if reliance <= 30 else "risk" if reliance > 60 else "moderate"
    if status == "strong":
        explanation = "Rarely relies on overdraft facilities."
    elif status == "moderate":
        explanation = "Occasional top-ups suggest some reliance on short-term credit."
    else:
        explanation = "Frequent, near-continuous reliance on overdraft."
    return Signal("Fuliza reliance", reliance, status, explanation, _FULIZA_RECOMMENDATIONS[status])


def _score_savings_activity(m: SavingsMetrics) -> Signal:
    rate = 0 if m.total_income == 0 else m.total_saved / m.total_income
    value = max(0, min(100, round(rate * 100 * 4)))  # ~25% savings rate maps to 100
    status = _status_for(value, 65, 30)
    if status == "strong":
        explanation = "Regular deposits relative to income."
    elif status == "moderate":
        explanation = "Below-average savings relative to income."
    else:
        explanation = "Very little set aside relative to income."
    return Signal("Savings activity", value, status, explanation, _SAVINGS_RECOMMENDATIONS[status])


def _overall_score(signals: list[Signal]) -> int:
    by_label = {s.label: s.value for s in signals}
    repayment = by_label["Repayment history"]
    fuliza = by_label["Fuliza reliance"]
    savings = by_label["Savings activity"]
    weighted = repayment * 0.5 + (100 - fuliza) * 0.2 + savings * 0.3
    return round(MIN_SCORE + (weighted / 100) * (MAX_SCORE - MIN_SCORE))


def _tip_for(signals: list[Signal]) -> str:
    rank = {"risk": 0, "moderate": 1, "strong": 2}
    worst = min(signals, key=lambda s: rank[s.status])
    if worst.label == "Fuliza reliance":
        return "Fewer Fuliza top-ups in the next 3 months could raise your score by ~20 points."
    if worst.label == "Savings activity":
        return "Setting aside even small, regular deposits could raise your score by ~15 points."
    return "Keeping up consistent, on-time payments over the next 3 months could raise your score by ~20 points."


def apply_hypothetical(
    current: PeriodMetrics,
    extra_savings: float = 0,
    fuliza_reduction_days: int = 0,
    extra_on_time_payments: int = 0,
) -> PeriodMetrics:
    """Applies a "what if" adjustment on top of the real current metrics, for
    the score simulator. Fixes missed payments before late ones (worst first,
    same priority a real user paying down arrears would naturally follow)."""
    missed = current.repayments.missed
    late = current.repayments.late
    on_time = current.repayments.on_time
    remaining = max(0, extra_on_time_payments)

    fix_missed = min(remaining, missed)
    missed -= fix_missed
    on_time += fix_missed
    remaining -= fix_missed

    fix_late = min(remaining, late)
    late -= fix_late
    on_time += fix_late

    fuliza_days = max(0, current.fuliza.days_active - max(0, fuliza_reduction_days))

    return PeriodMetrics(
        repayments=RepaymentMetrics(on_time=on_time, late=late, missed=missed),
        fuliza=FulizaMetrics(days_active=fuliza_days, period_days=current.fuliza.period_days),
        savings=SavingsMetrics(
            total_saved=current.savings.total_saved + max(0, extra_savings),
            total_income=current.savings.total_income,
        ),
    )


def compute_score(current: PeriodMetrics, previous: PeriodMetrics) -> ScoreResult:
    signals = [
        _score_repayment_history(current.repayments),
        _score_fuliza_reliance(current.fuliza),
        _score_savings_activity(current.savings),
    ]
    previous_signals = [
        _score_repayment_history(previous.repayments),
        _score_fuliza_reliance(previous.fuliza),
        _score_savings_activity(previous.savings),
    ]

    score = _overall_score(signals)
    previous_score = _overall_score(previous_signals)

    return ScoreResult(
        score=score,
        previous_score=previous_score,
        delta=score - previous_score,
        signals=signals,
        tip=_tip_for(signals),
    )
