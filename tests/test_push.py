import asyncio
import json
import re
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import BackgroundTasks

from app import push

from .conftest import FakeConn, fake_db_conn


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def service_account(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    sa = {
        "project_id": "test-project", "private_key_id": "kid-1", "private_key": pem,
        "client_email": "push@test-project.iam.gserviceaccount.com", "token_uri": "https://oauth2.googleapis.com/token",
    }
    path = tmp_path / "sa.json"
    path.write_text(json.dumps(sa))
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_FILE", str(path))
    monkeypatch.setitem(push._token_cache, "value", None)
    monkeypatch.setitem(push._token_cache, "expires", 0.0)
    return sa, key.public_key()


def test_without_firebase_credentials_nothing_is_sent(monkeypatch):
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_FILE", raising=False)
    assert run(push.send(["t" * 20], "title", "body", {})) == []


def test_oauth_assertion_is_signed_by_the_service_account(service_account):
    sa, public_key = service_account
    token = push.build_assertion(sa, now=int(time.time()))
    assert jwt.get_unverified_header(token)["kid"] == "kid-1"
    claims = jwt.decode(token, public_key, algorithms=["RS256"], audience=sa["token_uri"])
    assert claims["iss"] == sa["client_email"]
    assert claims["scope"] == "https://www.googleapis.com/auth/firebase.messaging"


def test_sends_to_each_device_and_reports_uninstalled_ones(service_account):
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "oauth-token", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer oauth-token"
        assert request.url.path == "/v1/projects/test-project/messages:send"
        msg = json.loads(request.content)["message"]
        sent.append(msg)
        if msg["token"] == "gone-token-xxxxxxxx":
            return httpx.Response(404, json={"error": {"details": [{"errorCode": "UNREGISTERED"}]}})
        return httpx.Response(200, json={"name": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dead = run(push.send(["live-token-xxxxxxxx", "gone-token-xxxxxxxx"], "T", "B", {"kind": "consent_request"}, client=client))
    assert dead == ["gone-token-xxxxxxxx"]
    assert [m["token"] for m in sent] == ["live-token-xxxxxxxx", "gone-token-xxxxxxxx"]
    assert sent[0]["data"] == {"kind": "consent_request"}


def test_lender_request_push_says_who_but_nothing_financial(monkeypatch):
    captured = {}

    async def fake_send(tokens, title, body, data, **_):
        captured.update(tokens=tokens, title=title, body=body, data=data)
        return ["dead-token"]

    conn = FakeConn({"from push_tokens": [{"token": "phone-token"}, {"token": "dead-token"}]})
    import app.db
    monkeypatch.setattr(app.db, "db_conn_service", lambda: fake_db_conn(conn)(None))
    monkeypatch.setattr(push, "send", fake_send)
    run(push.notify_lender_request("borrower-1", "Amani SACCO", "req-9"))

    assert captured["tokens"] == ["phone-token", "dead-token"]
    assert captured["title"] == "Amani SACCO asked to see your PesaScore"
    shown = captured["title"] + captured["body"]
    assert not re.search(r"\d{3}|KES|score of", shown), "no numbers or money on a lock screen"
    assert captured["data"] == {"kind": "consent_request", "requestId": "req-9"}
    assert any(sql.startswith("delete from push_tokens") for sql, _ in conn.calls)


def test_lender_request_queues_the_push_for_after_commit(monkeypatch):
    from app.lender_auth import AuthedLender
    from app.routers import lender as lender_router

    conn = FakeConn({"create_lender_consent_request": [{"id": "req-9"}], "from pending_consents": [{"user_id": "borrower-1"}]})
    monkeypatch.setattr(lender_router, "db_conn", fake_db_conn(conn))
    background = BackgroundTasks()
    me = AuthedLender(id="lender-1", email="l@x.test", org_name="Amani SACCO")
    body = lender_router.ConsentRequestBody(borrowerEmail="jane@x.test")
    # Bypass the rate-limit decorator; it needs a real request object.
    result = run(lender_router.create_consent_request.__wrapped__(None, body, background, me))
    assert result == {"ok": True, "requestId": "req-9"}
    task = background.tasks[0]
    assert task.func is lender_router.notify_lender_request
    assert task.args == ("borrower-1", "Amani SACCO", "req-9")
