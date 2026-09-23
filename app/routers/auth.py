import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import (
    SUPABASE_ANON_KEY,
    SUPABASE_URL,
    AuthedUser,
    clear_auth_cookies,
    get_current_user_optional,
    password_grant,
    refresh_session,
    set_auth_cookies,
)
from ..db import db_conn
from ..rate_limit import limiter
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


async def _create_account(email: str, password: str) -> dict:
    """Supabase signup + the account's own rows. Shared by the web (cookie)
    and mobile (token) signup endpoints. Returns Supabase's session payload."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/signup",
            json={"email": email, "password": password},
            headers={"apikey": SUPABASE_ANON_KEY},
        )

    if resp.status_code >= 400:
        # Supabase returns 422/400 for "already registered" and weak
        # passwords alike — surface its message, not a generic 500, so the
        # app can show something meaningful.
        raise HTTPException(status_code=400, detail=_supabase_error_message(resp))

    data = resp.json()
    if not data.get("access_token"):
        # "Confirm email" is on in the Supabase dashboard — signup succeeded
        # but there's no session yet. Both apps assume an immediate session,
        # so surface a clear error instead of getting silently stuck.
        raise HTTPException(
            status_code=422,
            detail="Account created, but email confirmation is required before signing in. "
            "Turn off \"Confirm email\" in Supabase Auth settings for this app's flow to work.",
        )

    user_id = data["user"]["id"]
    async with db_conn(user_id) as conn:
        await seed_new_account(conn, user_id, data["user"].get("email"))
    return data


@router.post("/v1/auth/signup")
async def signup(body: Credentials, response: Response):
    data = await _create_account(body.email, body.password)
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


# --- Mobile app (token) auth -------------------------------------------------
# Same Supabase accounts as the cookie endpoints above, but the tokens come
# back in the body for the app to keep in the OS keystore and send as
# "Authorization: Bearer ..." (see app/auth.py's get_current_user).


class RefreshBody(BaseModel):
    refreshToken: str


def _token_response(data: dict) -> dict:
    return {
        "accessToken": data["access_token"],
        "refreshToken": data["refresh_token"],
        "expiresIn": data.get("expires_in"),
        "email": (data.get("user") or {}).get("email"),
    }


@router.post("/v1/auth/token/signup")
@limiter.limit("5/minute")
async def token_signup(request: Request, body: Credentials):
    return _token_response(await _create_account(body.email, body.password))


@router.post("/v1/auth/token")
@limiter.limit("10/minute")
async def token_login(request: Request, body: Credentials):
    return _token_response(await password_grant(body.email, body.password))


@router.post("/v1/auth/token/refresh")
@limiter.limit("30/minute")
async def token_refresh(request: Request, body: RefreshBody):
    return _token_response(await refresh_session(body.refreshToken))


@router.post("/v1/auth/token/logout")
async def token_logout(request: Request):
    """Revokes the session server-side (the app also deletes its stored
    tokens). Best effort: a token that's already expired has nothing left to revoke."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SUPABASE_URL}/auth/v1/logout",
                headers={"apikey": SUPABASE_ANON_KEY, "Authorization": auth_header},
            )
    return {"ok": True}
