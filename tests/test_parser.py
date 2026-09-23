"""The parser and scorer against the synthetic demo statement (demo/), which
uses the same layout as a real Safaricom export. Expected numbers were checked
by hand against the generator in demo/make_synthetic_statement.py."""

import pytest

from app.pdf_parser import (
    StatementParseError,
    classify_rows,
    compute_cash_flow_series,
    extract_account_name,
    extract_text,
    names_match,
    parse_transactions,
    split_and_compute,
)
from app.scoring import compute_score

from .conftest import DEMO_PDF, DEMO_PDF_PASSWORD


@pytest.fixture(scope="module")
def text():
    return extract_text(DEMO_PDF.read_bytes(), None)


@pytest.fixture(scope="module")
def tagged(text):
    return classify_rows(parse_transactions(text))


def test_reads_every_transaction_and_the_name(text):
    rows = parse_transactions(text)
    assert len(rows) == 402
    assert rows[0]["date"] == "2026-03-01"
    assert extract_account_name(text) == "JANE DEMO WANJIKU"


def test_wrapped_details_are_joined(text):
    salary = [r for r in parse_transactions(text) if "ACME DEMO LOGISTICS" in r["details"]]
    assert len(salary) == 6
    assert all(r["amount"] > 60000 for r in salary)
    assert all("Original conversation ID is DEMO" in r["details"] for r in salary)


def test_password_protected_statement():
    data = DEMO_PDF_PASSWORD.read_bytes()
    with pytest.raises(StatementParseError, match="password"):
        extract_text(data, None)
    with pytest.raises(StatementParseError, match="Incorrect"):
        extract_text(data, "0000")
    assert len(parse_transactions(extract_text(data, "1234"))) == 402


def test_not_a_pdf():
    with pytest.raises(StatementParseError):
        extract_text(b"definitely not a pdf", None)


def test_classification(tagged):
    rows, groups = tagged
    by_tag = {}
    for r in rows:
        by_tag[r["tag"]] = by_tag.get(r["tag"], 0) + 1
    assert by_tag["repayment"] == 7  # 6 monthly SACCO payments + 1 failed attempt
    assert by_tag["internal"] == 10  # Fuliza "OverDraft of Credit Party" legs
    assert by_tag["ambiguous"] == 6

    # Only the bank paybill is asked about. Card subscriptions (GlobalPay),
    # utilities and shops are not.
    assert len(groups) == 1
    g = groups[0]
    assert g["businessName"] == "DEMO BANK Money Transfer"
    assert (g["count"], g["totalAmount"]) == (6, 24000.0)
    asked = {r["details"] for r in rows if r["tag"] == "ambiguous"}
    assert not any("GlobalPay" in d or "KENYA POWER" in d for d in asked)


def test_fuliza_repayment_is_not_a_loan_repayment(tagged):
    rows, _ = tagged
    od = [r for r in rows if "OD Loan Repayment" in r["details"]]
    assert od and all(r["tag"] == "fuliza" for r in od)


def test_score_when_bank_paybills_are_not_loans(tagged):
    rows, groups = tagged
    previous, current = split_and_compute(rows, {g["id"]: False for g in groups})
    assert (current.repayments.on_time, current.repayments.missed) == (3, 0)
    assert (previous.repayments.on_time, previous.repayments.missed) == (3, 1)
    result = compute_score(current, previous)
    assert (result.score, result.previous_score, result.delta) == (738, 706, 32)
    assert [s.status for s in result.signals] == ["strong", "strong", "moderate"]


def test_answers_change_the_score(tagged):
    rows, groups = tagged
    previous, current = split_and_compute(rows, {g["id"]: True for g in groups})
    assert current.repayments.on_time == 6  # 3 SACCO + 3 bank paybills
    assert compute_score(current, previous).delta == 1


def test_cash_flow_leaves_out_fuliza_ledger_legs(tagged):
    rows, _ = tagged
    series = compute_cash_flow_series(rows)
    assert len(series) == 8
    real_in = sum(r["amount"] for r in rows if r["tag"] != "internal" and r["amount"] > 0)
    assert sum(b["totalIn"] for b in series) == pytest.approx(real_in)


def test_empty_statement_is_an_error():
    with pytest.raises(StatementParseError):
        split_and_compute([], {})


def test_name_matching_ignores_case_and_spacing():
    assert names_match("Jane  Demo WANJIKU", "jane demo wanjiku")
    assert not names_match("Jane Demo Wanjiku", "Jane Wanjiku")
