"""What's left of the old in-memory Store now that real data lives in
Postgres (see app/db.py, app/seed.py): the default values a fresh account is
seeded with, and pending_reviews, which stays in-memory on purpose — it's an
in-flight, short-lived statement-classification session, not durable account
data, and this is a documented, accepted gap (unbounded in-memory, reset on
restart). Each session now carries a user_id so one account can't act on
another's pending review by guessing/enumerating a session id.
"""

from .scoring import FulizaMetrics, PeriodMetrics, RepaymentMetrics, SavingsMetrics

HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS

DEFAULT_CURRENT_METRICS = PeriodMetrics(
    repayments=RepaymentMetrics(on_time=11, late=1, missed=0),
    fuliza=FulizaMetrics(days_active=27, period_days=60),
    savings=SavingsMetrics(total_saved=9200, total_income=46000),
)
DEFAULT_PREVIOUS_METRICS = PeriodMetrics(
    repayments=RepaymentMetrics(on_time=9, late=2, missed=1),
    fuliza=FulizaMetrics(days_active=34, period_days=60),
    savings=SavingsMetrics(total_saved=6100, total_income=44000),
)

# Demo-only series for when no statement has been uploaded — labeled "Week N"
# rather than real dates, since there's no actual date range behind it. A real
# statement replaces this with genuinely bucketed transaction data (see
# pdf_parser.compute_cash_flow_series).
DEFAULT_CASH_FLOW = [
    {"label": "Week 1", "totalIn": 9000, "totalOut": 11000},
    {"label": "Week 2", "totalIn": 11000, "totalOut": 9500},
    {"label": "Week 3", "totalIn": 10500, "totalOut": 12000},
    {"label": "Week 4", "totalIn": 12000, "totalOut": 10000},
    {"label": "Week 5", "totalIn": 11500, "totalOut": 9800},
    {"label": "Week 6", "totalIn": 13000, "totalOut": 11500},
    {"label": "Week 7", "totalIn": 12500, "totalOut": 10800},
    {"label": "Week 8", "totalIn": 14000, "totalOut": 12000},
]

# Seeded so a brand-new account isn't empty on first load — same product
# decision the old Store made.
DEFAULT_PENDING_CONSENT = {
    "lender_name": "Amani SACCO",
    "grant_duration_days": 30,
    "will_share": ["Repayment history summary", "Savings activity summary", "Account age"],
    "wont_share": ["Full transaction amounts", "Contact list", "Balances on other accounts"],
}
DEFAULT_GRANT_LENDER_NAME = "Amani SACCO"
DEFAULT_GRANT_DURATION_MS = 22 * HOUR_MS

# Demo-only pool for "simulate an incoming request" (see routers/consent.py).
SIMULATED_LENDER_POOL = [
    "Mwalimu SACCO",
    "Stima SACCO",
    "Safaricom Co-operative SACCO",
    "KCB M-Pesa",
    "Branch",
    "Tala",
    "Zenka",
]

# sessionId -> {"user_id", "rows"} awaiting the user's yes/no answers before a
# score can be computed. In-memory, unbounded, resets on restart — documented,
# accepted gap (see backend CLAUDE.md "Not done yet").
pending_reviews: dict[str, dict] = {}
