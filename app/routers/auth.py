import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..auth import (
    SUPABASE_ANON_KEY,
    SUPABASE_URL,
    AuthedUser,
    clear_auth_cookies,
    get_current_user_optional,
    password_grant,
    set_auth_cookies,
)
from ..db import db_conn
from ..seed import seed_new_account

router = APIRouter()


class Credentials(BaseModel):
    email: str
    password: str


def _supabase_error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return "Something went wrong. Please try again."
    return body.get("error_description") or body.get("msg") or body.get("error") or "Something went wrong. Please try again."


@router.post("/v1/auth/signup")
async def signup(body: Credentials, response: Response):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/signup",
            json={"email": body.email, "password": body.password},
            headers={"apikey": SUPABASE_ANON_KEY},
        )

    if resp.status_code >= 400:
        # Supabase returns 422/400 for "already registered" and weak
        # passwords alike — surface its message, not a generic 500, so the
        # frontend's TextField error state can show something meaningful.
        raise HTTPException(status_code=400, detail=_supabase_error_message(resp))

    data = resp.json()
    if not data.get("access_token"):
        # "Confirm email" is on in the Supabase dashboard — signup succeeded
        # but there's no session yet. This app's UX assumes an immediate
        # session (straight to Terms), so this should be turned off for now;
        # surfacing a clear error beats silently getting stuck at PinSetup.
        raise HTTPException(
            status_code=422,
            detail="Account created, but email confirmation is required before signing in. "
            "Turn off \"Confirm email\" in Supabase Auth settings for this app's flow to work.",
        )

    user_id = data["user"]["id"]
    async with db_conn(user_id) as conn:
        await seed_new_account(conn, user_id, data["user"].get("email"))

    set_auth_cookies(response, data["access_token"], data["refresh_token"])
    return {"email": data["user"].get("email")}


@router.post("/v1/auth/login")
async def login(body: Credentials, response: Response):
    data = await password_grant(body.email, body.password)
    set_auth_cookies(response, data["access_token"], data["refresh_token"])
    return {"email": data["user"].get("email")}


@router.post("/v1/auth/logout")
async def logout(response: Response):
    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/v1/auth/session")
async def get_session(user: AuthedUser | None = Depends(get_current_user_optional)):
    if user is None:
        return {"authenticated": False}
    return {"authenticated": True, "email": user.email}
