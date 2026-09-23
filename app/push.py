"""Push notifications through Firebase Cloud Messaging (HTTP v1 API).

Needs a Firebase service account key (Firebase console -> Project settings ->
Service accounts -> Generate new private key). Point FIREBASE_SERVICE_ACCOUNT_FILE
at the downloaded JSON. Without it, sending is a logged no-op, so local dev
and CI work without Firebase.

The OAuth token is minted here with PyJWT (RS256, already a dependency for
Supabase JWTs) instead of pulling in google-auth.

Lock-screen privacy: callers put no score, amount or transaction detail in a
push. Say who wants something, and let the app show the rest after unlock.
"""

import json
import logging
import os
import time

import httpx
import jwt

log = logging.getLogger("pesascore.push")

_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_token_cache: dict = {"value": None, "expires": 0.0}


def _service_account() -> dict | None:
    path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_FILE")
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_assertion(sa: dict, now: int) -> str:
    return jwt.encode(
        {"iss": sa["client_email"], "scope": _SCOPE, "aud": sa["token_uri"], "iat": now, "exp": now + 3600},
        sa["private_key"],
        algorithm="RS256",
        headers={"kid": sa["private_key_id"]},
    )


async def _access_token(client: httpx.AsyncClient, sa: dict) -> str:
    if _token_cache["value"] and _token_cache["expires"] > time.time() + 60:
        return _token_cache["value"]
    now = int(time.time())
    resp = await client.post(
        sa["token_uri"],
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": build_assertion(sa, now)},
    )
    resp.raise_for_status()
    body = resp.json()
    _token_cache.update(value=body["access_token"], expires=now + int(body.get("expires_in", 3600)))
    return body["access_token"]


def _is_dead_token(resp: httpx.Response) -> bool:
    """FCM says the install is gone (app removed, token rotated)."""
    if resp.status_code == 404:
        return True
    try:
        details = resp.json()["error"].get("details", [])
    except Exception:
        return False
    return any(d.get("errorCode") in ("UNREGISTERED", "INVALID_ARGUMENT") for d in details)


async def send(tokens: list[str], title: str, body: str, data: dict[str, str], *, client: httpx.AsyncClient | None = None) -> list[str]:
    """Sends one notification to each token. Returns the tokens FCM says are
    dead, for the caller to delete. Never raises: a failed push must not
    fail the request that triggered it."""
    sa = _service_account()
    if sa is None:
        log.info("push skipped (FIREBASE_SERVICE_ACCOUNT_FILE not set): %s", title)
        return []
    if not tokens:
        return []
    url = f"https://fcm.googleapis.com/v1/projects/{sa['project_id']}/messages:send"
    dead: list[str] = []
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        auth = {"Authorization": f"Bearer {await _access_token(client, sa)}"}
        for token in tokens:
            resp = await client.post(url, headers=auth, json={"message": {
                "token": token,
                "notification": {"title": title, "body": body},
                "data": data,
                "android": {"priority": "high"},
            }})
            if resp.status_code >= 300:
                if _is_dead_token(resp):
                    dead.append(token)
                else:
                    log.warning("push failed %s: %s", resp.status_code, resp.text[:200])
    except Exception:
        log.exception("push send failed")
    finally:
        if own_client:
            await client.aclose()
    return dead


async def notify_lender_request(borrower_id: str, lender_name: str, request_id: str) -> None:
    """Background task after a lender sends a request. Reads the borrower's
    tokens with the service connection: the lender's own RLS-scoped session
    can't (and shouldn't) see another user's devices."""
    from .db import db_conn_service

    async with db_conn_service() as conn:
        rows = await (await conn.execute("select token from push_tokens where user_id = %s", (borrower_id,))).fetchall()
    dead = await send(
        [r["token"] for r in rows],
        title=f"{lender_name} asked to see your PesaScore",
        body="Open PesaScore to see exactly what they'd see, then say yes or no.",
        data={"kind": "consent_request", "requestId": request_id},
    )
    if dead:
        async with db_conn_service() as conn:
            await conn.execute("delete from push_tokens where token = any(%s)", (dead,))
