from app.scoring import (
    MAX_SCORE,
    MIN_SCORE,
    FulizaMetrics,
    PeriodMetrics,
    RepaymentMetrics,
    SavingsMetrics,
    apply_hypothetical,
    compute_score,
)


def metrics(on_time=10, late=0, missed=0, fuliza_days=0, period=60, saved=0.0, income=40000.0):
    return PeriodMetrics(
        repayments=RepaymentMetrics(on_time=on_time, late=late, missed=missed),
        fuliza=FulizaMetrics(days_active=fuliza_days, period_days=period),
        savings=SavingsMetrics(total_saved=saved, total_income=income),
    )


def test_best_and_worst_hit_the_ends_of_the_range():
    best = metrics(on_time=10, fuliza_days=0, saved=10000)  # 25% savings rate maps to 100
    worst = metrics(on_time=0, missed=10, fuliza_days=60, saved=0)
    assert compute_score(best, best).score == MAX_SCORE
    assert compute_score(worst, worst).score == MIN_SCORE


def test_weights_are_half_repayment_fifth_fuliza_rest_savings():
    # Repayment 100, Fuliza 0% reliance, savings 0 -> 0.5 + 0.2 = 70% of the range.
    m = metrics(on_time=10, fuliza_days=0, saved=0)
    assert compute_score(m, m).score == round(MIN_SCORE + 0.7 * (MAX_SCORE - MIN_SCORE))


def test_no_repayment_history_is_not_a_penalty():
    m = metrics(on_time=0)
    assert compute_score(m, m).signals[0].value == 100


def test_every_signal_explains_itself():
    for m in (metrics(), metrics(on_time=1, missed=5, fuliza_days=50)):
        for s in compute_score(m, m).signals:
            assert s.explanation and s.recommendation


def test_delta_is_current_minus_previous():
    r = compute_score(metrics(on_time=10), metrics(on_time=5, missed=5))
    assert r.delta == r.score - r.previous_score > 0


def test_simulator_fixes_missed_payments_before_late_ones():
    m = metrics(on_time=5, late=2, missed=1)
    h = apply_hypothetical(m, extra_on_time_payments=2)
    assert (h.repayments.on_time, h.repayments.late, h.repayments.missed) == (7, 1, 0)


def test_simulator_cannot_go_below_zero_fuliza_days():
    h = apply_hypothetical(metrics(fuliza_days=5), fuliza_reduction_days=50)
    assert h.fuliza.days_active == 0
