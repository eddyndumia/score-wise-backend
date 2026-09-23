"""The rules that enforce promises from the public post
(docs/REQUIREMENTS.md). Run against a fake connection, no database."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from app import auth, metrics_repo, reviews_repo, seed
from app.routers import consent
from app.routers.consent import ConsentResponse

from .conftest import FakeConn, fake_db_conn

USER = auth.AuthedUser(id="00000000-0000-0000-0000-000000000001", email="jane@pesascore.test")


def run(coro):
    return asyncio.run(coro)


# B14: no made-up numbers ---------------------------------------------------------


def test_new_account_gets_a_profile_and_nothing_else():
    conn = FakeConn()
    run(seed.seed_new_account(conn, USER.id, USER.email))
    assert len(conn.calls) == 1
    assert "insert into profiles" in conn.calls[0][0]


def test_no_metrics_means_no_score_not_a_default():
    conn = FakeConn({"from period_metrics": []})
    assert run(metrics_repo.load_period_metrics(conn, USER.id)) is None
    with pytest.raises(HTTPException) as e:
        run(metrics_repo.require_period_metrics(conn, USER.id))
    assert e.value.status_code == 404
    assert e.value.detail["code"] == "no_score"


def test_half_a_statement_is_not_a_score():
    only_current = [{
        "period": "current",
        "repayments": {"on_time": 1, "late": 0, "missed": 0},
        "fuliza": {"days_active": 0, "period_days": 30},
        "savings": {"total_saved": 0, "total_income": 1},
    }]
    assert run(metrics_repo.load_period_metrics(FakeConn({"from period_metrics": only_current}), USER.id)) is None


# B13: the borrower sees the score before any lender --------------------------------


def test_cannot_approve_a_lender_before_having_a_score(monkeypatch):
    conn = FakeConn({
        "from pending_consents": [{"lender_name": "Amani", "lender_id": None, "grant_duration_days": 30, "will_share": []}],
        "from period_metrics": [],
    })
    monkeypatch.setattr(consent, "db_conn", fake_db_conn(conn))
    with pytest.raises(HTTPException) as e:
        run(consent.respond_to_consent("req-1", ConsentResponse(approve=True), USER))
    assert e.value.status_code == 409
    assert e.value.detail["code"] == "no_score"
    assert not any("insert into grants_table" in sql for sql, _ in conn.calls)
    assert not any("status = 'approved'" in sql for sql, _ in conn.calls)


def test_saying_no_never_needs_a_score(monkeypatch):
    conn = FakeConn({
        "from pending_consents": [{"lender_name": "Amani", "lender_id": None, "grant_duration_days": 30, "will_share": []}],
    })
    monkeypatch.setattr(consent, "db_conn", fake_db_conn(conn))
    assert run(consent.respond_to_consent("req-1", ConsentResponse(approve=False), USER)) == {"ok": True, "grant": None}
    assert any("status = 'denied'" in sql for sql, _ in conn.calls)


# B3: keep only the numbers the score needs ------------------------------------------


def test_review_session_stores_numbers_not_transaction_details():
    row = {
        "date": "2026-04-15", "amount": -4000.0, "status": "Completed", "tag": "ambiguous",
        "isFuliza": False, "groupId": "400000:DEMO BANK",
        "details": "Pay Bill to 400000 - DEMO BANK Money Transfer Acc. 0000111",
    }
    conn = FakeConn()
    run(reviews_repo.save_pending_review(conn, USER.id, "s1", [row]))
    stored = json.loads(conn.calls[0][1][2])
    assert stored == [{k: row[k] for k in ("date", "amount", "status", "tag", "isFuliza", "groupId")}]
    assert "DEMO BANK Money Transfer Acc" not in conn.calls[0][1][2]


# Mobile bearer auth ----------------------------------------------------------------


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""})


def test_bearer_token_is_accepted_for_borrowers(monkeypatch):
    monkeypatch.setattr(auth, "verify_access_token", lambda token: USER if token == "good" else None)
    assert run(auth.get_current_user(_request({"Authorization": "Bearer good"}), Response())) == USER


def test_expired_bearer_token_says_so(monkeypatch):
    def expired(_token):
        raise auth.jwt.ExpiredSignatureError()

    monkeypatch.setattr(auth, "verify_access_token", expired)
    with pytest.raises(HTTPException) as e:
        run(auth.get_current_user(_request({"Authorization": "Bearer old"}), Response()))
    assert e.value.status_code == 401
    assert e.value.detail["code"] == "token_expired"


def test_lender_routes_ignore_bearer_tokens(monkeypatch):
    monkeypatch.setattr(auth, "verify_access_token", lambda token: USER)
    with pytest.raises(HTTPException) as e:
        run(auth.get_current_user(_request({"Authorization": "Bearer good"}), Response(), allow_bearer=False))
    assert e.value.status_code == 401


# Right to erasure ------------------------------------------------------------------


def test_reset_wipes_every_table_holding_borrower_data(monkeypatch):
    from app.routers import account

    conn = FakeConn()
    monkeypatch.setattr(account, "db_conn", fake_db_conn(conn))
    run(account.delete_account(Response(), USER))
    wiped = {sql.split("delete from ")[1].split()[0] for sql, _ in conn.calls if sql.startswith("delete from")}
    assert wiped == {
        "period_metrics", "cash_flow", "pending_consents", "grants_table",
        "savings_goals", "notifications", "pending_reviews",
    }
