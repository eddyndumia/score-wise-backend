"""Parses M-Pesa statement PDFs into the PeriodMetrics shape scoring.py expects.

Rewritten after testing against a real Safaricom statement export (previously
only tested against a synthetic one — see CLAUDE.md for what was wrong with
the first version). Real statements:

- Wrap transaction "Details" text across multiple physical lines before the
  Completed/Failed status appears.
- Show exactly TWO trailing numbers per transaction, not three: a single
  SIGNED amount (negative = money out, positive = money in) followed by the
  resulting balance. There is no separate Paid In / Withdrawn column per row
  despite the header listing both — confirmed against ~20 real examples.
- Represent Fuliza (overdraft) activity as multiple ledger "legs" sharing one
  receipt code (e.g. an internal "OverDraft of Credit Party" credit leg paired
  with the actual customer-facing debit leg). The internal leg is not real
  cash flow and must not be counted as income, but both legs correctly mark
  the date as Fuliza-active.
- Label Fuliza's own overdraft repayment as "OD Loan Repayment", which
  otherwise collides with the "loan repayment" keyword meant for actual
  SACCO/lender paybill repayments — Fuliza-matching legs are excluded from
  repayment-history classification to avoid conflating the two.

Two more things added after testing against real data:

- Transactions that plausibly could be loan repayments but don't match a known
  SACCO/lender pattern (generic "Pay Bill"/"Business Payment" bank transfers)
  are surfaced as ambiguous *groups* (by counterparty) for the user to confirm,
  rather than silently guessed either way — see classify_rows().
- The statement header's "Customer Name" is extracted so the backend can
  verify a later upload belongs to the same person as the first one — see
  extract_account_name() and NameMismatchError.
"""

import io
import re
from datetime import datetime, timedelta

from pypdf import PdfReader

from .scoring import FulizaMetrics, PeriodMetrics, RepaymentMetrics, SavingsMetrics


class StatementParseError(Exception):
    pass


class NameMismatchError(StatementParseError):
    def __init__(self, expected_name: str, found_name: str):
        self.expected_name = expected_name
        self.found_name = found_name
        super().__init__(f"This statement belongs to {found_name}, not {expected_name}.")


# Matches the start of a transaction record: a receipt code + timestamp at the
# start of a line. Records are delimited by consecutive matches of this, since
# the "Details" text between one record's timestamp and its Completed/Failed
# status can span multiple lines.
RECORD_START_RE = re.compile(
    r"^([A-Z0-9]{8,12})\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})",
    re.MULTILINE,
)

# Within a record's text: status, then a signed amount, then the balance.
STATUS_AMOUNT_RE = re.compile(
    r"(Completed|Failed)\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})"
)

CUSTOMER_NAME_RE = re.compile(r"Customer Name:\s*(.+)")

# Pulls a counterparty (paybill number + business name) out of "Details" text
# for grouping ambiguous transactions, e.g. "Pay Bill to 400200 - Co-operative
# Bank Money Transfer Acc. 40017061" -> ("400200", "Co-operative Bank Money Transfer").
COUNTERPARTY_RE = re.compile(r"(?:to|from)\s+(\d{3,10})\s*-\s*(.+?)(?:\s+Acc\.|\s+via API|$)")

FULIZA_KEYWORDS = ["fuliza", "overdraft", "overdraw"]

# Deliberately NOT "pay bill" generically — real statements show M-Pesa's card
# paybill (903470, "M-PESA GlobalPay Acc.") used for ordinary subscriptions
# (Netflix, Google, Coursera, etc.), which a generic "pay bill" match would
# wrongly count as loan repayment. Kept to: SACCOs (reliably named "...SACCO..."
# per SASRA's 176 licensed societies) and named digital lenders confirmed via
# web search (Sep 2026) — Tala, Branch, Zenka, Timiza (Absa's), OKash, Mogo,
# Izwe — plus their known paybill numbers as a fallback when the business name
# in the statement doesn't literally contain the lender's brand name. "Loan
# disbursement" is excluded on purpose: receiving a loan isn't a repayment
# event, so counting it here would misrepresent repayment behavior.
REPAYMENT_KEYWORDS = [
    "sacco",
    "loan repayment",
    "tala",
    "zenka",
    "timiza",
    "okash",
    "mogo",
    "izwe",
    "branch loan",
    "851900",  # Tala paybill
    "247988",  # Branch paybill
    "300067",  # Timiza (Absa) paybill
]
SAVINGS_KEYWORDS = ["m-shwari", "mshwari", "kcb m-pesa", "lock savings", "savings"]
# Internal ledger legs Safaricom prints for its own Fuliza bookkeeping — not
# real cash flow, so excluded from income/repayment/savings totals entirely.
INTERNAL_LEDGER_KEYWORDS = ["overdraft of credit party"]
# M-Pesa's own card-linked paybill, used for ordinary subscriptions — never a
# loan, so excluded from the ambiguous bucket rather than asked about.
NOT_LOAN_KEYWORDS = ["globalpay", "903470"]

# What actually makes a generic Pay Bill/Business Payment worth *asking about*.
# Tested against a real 2-year, ~7,400-transaction statement: without this
# allowlist, "ambiguous" caught 63 counterparties including Spotify, KPLC,
# Jumia, Betika, and Kenya Airways — unusable. Restricting to bank/finance-
# sounding names cut that to the genuine bank-paybill cases (Equity, KCB,
# NCBA, Absa, Co-operative, Family Bank, IM Bank, Consolidated Bank), which
# really are ambiguous — a bank paybill could be a loan repayment, a savings
# transfer, or an unrelated bank service, and M-Pesa data alone can't tell.
BANK_OR_FINANCE_KEYWORDS = [
    "bank", "kcb", "equity", "co-operative", "cooperative",
    "loan", "credit", "finance", "microfinance", "capital",
]


def _to_amount(raw: str) -> float:
    return float(raw.replace(",", ""))


def extract_text(file_bytes: bytes, password: str | None) -> str:
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        if reader.is_encrypted:
            if not password:
                raise StatementParseError("Statement is password-protected but no password was provided.")
            if reader.decrypt(password) == 0:
                raise StatementParseError("Incorrect statement password.")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except StatementParseError:
        raise
    except Exception as e:
        raise StatementParseError("Couldn't read this file as a PDF.") from e


def extract_account_name(text: str) -> str | None:
    m = CUSTOMER_NAME_RE.search(text)
    return m.group(1).strip() if m else None


def names_match(a: str, b: str) -> bool:
    norm = lambda s: " ".join(s.lower().split())  # noqa: E731
    return norm(a) == norm(b)


def parse_transactions(text: str) -> list[dict]:
    starts = list(RECORD_START_RE.finditer(text))
    rows = []

    for i, m in enumerate(starts):
        record_end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        record_text = text[m.start():record_end]

        status_match = STATUS_AMOUNT_RE.search(record_text)
        if not status_match:
            continue  # incomplete/unrecognized record — skip rather than guess

        details_raw = record_text[m.end() - m.start():status_match.start()]
        details = " ".join(details_raw.split())  # collapse newlines/whitespace

        rows.append(
            {
                "date": m.group(2),
                "details": details,
                "status": status_match.group(1),
                "amount": _to_amount(status_match.group(2)),
            }
        )

    return rows


def _counterparty_key(details: str) -> tuple[str, str]:
    m = COUNTERPARTY_RE.search(details)
    if m:
        return m.group(1), m.group(2).strip()
    return "", details.strip()[:60]


def _is_ambiguous_candidate(details_lower: str, amount: float) -> bool:
    if amount >= 0:
        return False  # a repayment is money going out; incoming transfers can't be one
    if any(k in details_lower for k in NOT_LOAN_KEYWORDS):
        return False
    if not any(k in details_lower for k in BANK_OR_FINANCE_KEYWORDS):
        return False
    return "pay bill" in details_lower or "business payment" in details_lower


def classify_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Tags each row with a definite category, or collects it into an ambiguous
    group (by counterparty) for the user to confirm. Returns (tagged_rows, groups).
    A tagged row's "tag" is one of: internal, fuliza, repayment, savings,
    ambiguous, ignore. Ambiguous rows also carry a "groupId"."""
    groups: dict[str, dict] = {}
    tagged = []

    for r in rows:
        details_lower = r["details"].lower()
        is_internal_ledger = any(k in details_lower for k in INTERNAL_LEDGER_KEYWORDS)
        is_fuliza = any(k in details_lower for k in FULIZA_KEYWORDS)
        is_repayment = not is_fuliza and any(k in details_lower for k in REPAYMENT_KEYWORDS)
        is_savings = any(k in details_lower for k in SAVINGS_KEYWORDS)

        group_id = None
        if is_internal_ledger:
            tag = "internal"
        elif is_fuliza:
            tag = "fuliza"
        elif is_repayment:
            tag = "repayment"
        elif is_savings:
            tag = "savings"
        elif _is_ambiguous_candidate(details_lower, r["amount"]):
            paybill, name = _counterparty_key(r["details"])
            group_id = f"{paybill}:{name}".strip(":") or name
            g = groups.setdefault(
                group_id,
                {
                    "id": group_id,
                    "businessName": name,
                    "paybillNumber": paybill or None,
                    "count": 0,
                    "totalAmount": 0.0,
                    "sampleDetail": r["details"][:120],
                },
            )
            g["count"] += 1
            g["totalAmount"] += abs(r["amount"])
            tag = "ambiguous"
        else:
            tag = "ignore"

        tagged.append({**r, "tag": tag, "isFuliza": is_fuliza, "groupId": group_id})

    return tagged, list(groups.values())


def _metrics_from_tagged_rows(rows: list[dict], overrides: dict[str, bool]) -> PeriodMetrics:
    dates = sorted(r["date"] for r in rows)
    period_days = max(1, (datetime.fromisoformat(dates[-1]) - datetime.fromisoformat(dates[0])).days)

    fuliza_dates = set()
    on_time = missed = 0
    total_saved = 0.0
    total_income = 0.0

    for r in rows:
        paid_in = r["amount"] if r["amount"] > 0 else 0.0
        withdrawn = -r["amount"] if r["amount"] < 0 else 0.0
        tag = r["tag"]

        if r["isFuliza"]:
            fuliza_dates.add(r["date"])

        if tag == "internal":
            continue  # not real cash flow

        if tag == "repayment":
            if r["status"] == "Completed":
                on_time += 1
            else:
                missed += 1
        elif tag == "ambiguous" and overrides.get(r["groupId"]) is True:
            if r["status"] == "Completed":
                on_time += 1
            else:
                missed += 1

        if tag == "savings":
            total_saved += max(paid_in, withdrawn)

        total_income += paid_in

    return PeriodMetrics(
        repayments=RepaymentMetrics(on_time=on_time, late=0, missed=missed),
        fuliza=FulizaMetrics(days_active=len(fuliza_dates), period_days=period_days),
        savings=SavingsMetrics(total_saved=total_saved, total_income=total_income),
    )


def split_and_compute(
    tagged_rows: list[dict], overrides: dict[str, bool]
) -> tuple[PeriodMetrics, PeriodMetrics]:
    """Splits classified transactions by date midpoint so a single statement can
    still produce a (previous, current) pair for the score delta — a real
    integration would compare against the borrower's actual prior statement
    instead. Returns (previous, current)."""
    if not tagged_rows:
        raise StatementParseError("No recognizable M-Pesa transactions found in this statement.")

    sorted_rows = sorted(tagged_rows, key=lambda r: r["date"])
    mid = len(sorted_rows) // 2
    first_half = sorted_rows[:mid] or sorted_rows
    second_half = sorted_rows[mid:] or sorted_rows

    return (
        _metrics_from_tagged_rows(first_half, overrides),
        _metrics_from_tagged_rows(second_half, overrides),
    )


def compute_cash_flow_series(tagged_rows: list[dict], buckets: int = 8) -> list[dict]:
    """Buckets real transactions (excludes "internal" Fuliza ledger legs, which
    aren't real cash flow) into roughly-equal time windows across the whole
    statement, summing money in vs. out per bucket. Computed once at upload
    time regardless of how ambiguous groups get classified — whether a payment
    is "a loan repayment" doesn't change whether it was money leaving the
    account, so this doesn't need to wait on review answers."""
    real_rows = [r for r in tagged_rows if r["tag"] != "internal"]
    if not real_rows:
        return []

    sorted_rows = sorted(real_rows, key=lambda r: r["date"])
    start = datetime.fromisoformat(sorted_rows[0]["date"])
    end = datetime.fromisoformat(sorted_rows[-1]["date"])
    span_days = max(1, (end - start).days)
    bucket_days = max(1, -(-span_days // buckets))  # ceil division

    series = []
    for i in range(buckets):
        bucket_start = start + timedelta(days=i * bucket_days)
        bucket_end = end + timedelta(days=1) if i == buckets - 1 else start + timedelta(days=(i + 1) * bucket_days)
        total_in = total_out = 0.0
        for r in sorted_rows:
            d = datetime.fromisoformat(r["date"])
            if bucket_start <= d < bucket_end:
                if r["amount"] > 0:
                    total_in += r["amount"]
                else:
                    total_out += -r["amount"]
        series.append(
            {
                "label": bucket_start.strftime("%d %b"),
                "totalIn": round(total_in, 2),
                "totalOut": round(total_out, 2),
            }
        )
    return series
