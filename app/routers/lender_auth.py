"""Lender signup/login/logout/session — mirrors routers/auth.py's shape for
borrowers, but deliberately a separate router rather than a branch inside
the existing one: signup here seeds a `lenders` row (app/seed.py's
seed_new_lender), never a borrower's profiles/period_metrics/etc, and every
handler uses the lender cookie pair (app/auth.py's COOKIE_LENDER_*) so a
lender session never collides with a borrower session in the same browser.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..auth import (
    SUPABASE_ANON_KEY,
    SUPABASE_URL,
    COOKIE_LENDER_ACCESS,
    COOKIE_LENDER_REFRESH,
    clear_auth_cookies,
    password_grant,
    set_auth_cookies,
)
from ..db import db_conn
from ..lender_auth import AuthedLender, get_current_lender_optional
from ..seed import seed_new_lender

router = APIRouter()


class LenderCredentials(BaseModel):
    email: str
    password: str
    orgName: str


class LenderLogin(BaseModel):
    email: str
    password: str


def _supabase_error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return "Something went wrong. Please try again."
    return body.get("error_description") or body.get("msg") or body.get("error") or "Something went wrong. Please try again."


@router.post("/v1/lender/auth/signup")
async def lender_signup(body: LenderCredentials, response: Response):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/signup",
            json={"email": body.email, "password": body.password},
            headers={"apikey": SUPABASE_ANON_KEY},
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=_supabase_error_message(resp))

    data = resp.json()
    if not data.get("access_token"):
        raise HTTPException(
            status_code=422,
            detail="Account created, but email confirmation is required before signing in. "
            "Turn off \"Confirm email\" in Supabase Auth settings for this app's flow to work.",
        )

    user_id = data["user"]["id"]
    async with db_conn(user_id) as conn:
        await seed_new_lender(conn, user_id, body.orgName)

    set_auth_cookies(
        response, data["access_token"], data["refresh_token"], access_cookie=COOKIE_LENDER_ACCESS, refresh_cookie=COOKIE_LENDER_REFRESH
    )
    return {"email": data["user"].get("email"), "orgName": body.orgName}


@router.post("/v1/lender/auth/login")
async def lender_login(body: LenderLogin, response: Response):
    data = await password_grant(body.email, body.password)
    user_id = data["user"]["id"]

    async with db_conn(user_id) as conn:
        row = await (await conn.execute("select org_name from lenders where id = %s", (user_id,))).fetchone()
    if row is None:
        # A real borrower's own credentials are perfectly valid Supabase
        # Auth credentials — this is what stops a borrower's account from
        # being usable as a lender login, without needing a separate
        # Supabase project.
        raise HTTPException(status_code=403, detail="This account is not registered as a lender")

    set_auth_cookies(
        response, data["access_token"], data["refresh_token"], access_cookie=COOKIE_LENDER_ACCESS, refresh_cookie=COOKIE_LENDER_REFRESH
    )
    return {"email": data["user"].get("email"), "orgName": row["org_name"]}


@router.post("/v1/lender/auth/logout")
async def lender_logout(response: Response):
    clear_auth_cookies(response, access_cookie=COOKIE_LENDER_ACCESS, refresh_cookie=COOKIE_LENDER_REFRESH)
    return {"ok": True}


@router.get("/v1/lender/auth/session")
async def lender_session(lender: AuthedLender | None = Depends(get_current_lender_optional)):
    if lender is None:
        return {"authenticated": False}
    return {"authenticated": True, "email": lender.email, "orgName": lender.org_name}
