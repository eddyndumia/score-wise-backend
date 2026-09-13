"""Canonical consent categories a grant's will_share can list. A grant's
will_share is read from its own row (not assumed fixed for every grant) and
applied specifically to that grant — see routers/lender.py. Display labels
are exactly what the consumer app has always shown (zero frontend changes
there); category keys are new, internal, and are what actually drives
enforcement in the lender-facing endpoints.
"""

from .scoring import Signal

CONSENT_CATEGORIES: dict[str, dict] = {
    "repayment_history": {"label": "Repayment history summary", "signal_label": "Repayment history"},
    "savings_activity": {"label": "Savings activity summary", "signal_label": "Savings activity"},
    "fuliza_reliance": {"label": "Fuliza reliance summary", "signal_label": "Fuliza reliance"},
    # Gates accountAgeDays on the lender-facing applicant response, not a
    # scoring.py Signal — there's nothing to filter out of `signals` for it.
    "account_age": {"label": "Account age", "signal_label": None},
}

# The one, fixed, platform-defined sharing package every real lender
# request offers — see supabase/schema.sql's create_lender_consent_request.
# There's no per-field consent picker UI anywhere in this app (approval is
# a single Allow/Deny), so a lender never gets to choose or narrow this.
STANDARD_WILL_SHARE = ["repayment_history", "savings_activity", "fuliza_reliance", "account_age"]


def category_labels(keys: list[str]) -> list[str]:
    return [CONSENT_CATEGORIES[k]["label"] for k in keys if k in CONSENT_CATEGORIES]


def filter_signals(signals: list[Signal], will_share_keys: list[str]) -> list[Signal]:
    allowed_signal_labels = {
        CONSENT_CATEGORIES[k]["signal_label"]
        for k in will_share_keys
        if CONSENT_CATEGORIES.get(k, {}).get("signal_label")
    }
    return [s for s in signals if s.label in allowed_signal_labels]
