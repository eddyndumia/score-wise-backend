"""Supabase Auth, brokered entirely through this backend.

The frontend never talks to Supabase directly and never sees a Supabase
token as JSON — it only ever sees httpOnly cookies this backend sets. That's
the thing that actually satisfies the "sessions as httpOnly, Secure,
SameSite cookies — never in localStorage" requirement from this project's
CLAUDE.md: a token the frontend could read out of a JS-accessible response
body could just as easily end up in localStorage by an unrelated future
change, but it can never read an httpOnly cookie at all.

This module owns: reading/writing those cookies, verifying the JWT locally
(no network round trip per request), and transparently refreshing an expired
access token via Supabase's own refresh endpoint.
"""

import os
from dataclasses import dataclass

import httpx
import jwt
from fastapi import HTTPException, Request, Response

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

# This project uses Supabase's newer asymmetric JWT signing keys (ES256),
# not the legacy shared HS256 secret — confirmed by decoding a real access
# token's header (alg: ES256, a kid present). Verification therefore needs
# the project's public JWKS, not a shared secret. PyJWKClient fetches and
# caches it, matching tokens to the right key by `kid`.
_jwk_client = jwt.PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")

# secure=False + samesite=lax is required for plain-http localhost dev (both
# localhost:5173 and localhost:8000 count as the same "site" despite the
# different ports, so Lax cookies still flow). A real deployment puts the
# frontend and backend on genuinely different domains (e.g. Netlify +
# Render), which is actually cross-site — SameSite=None + Secure=True is
# required there, or the browser silently drops the cookie on the frontend's
# cross-origin fetch. Set COOKIE_SECURE=true and COOKIE_SAMESITE=none in that
# environment's variables.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "lax").lower()
COOKIE_ACCESS = "sw_access_token"
COOKIE_REFRESH = "sw_refresh_token"
# Lenders are a second, distinct authenticated principal (see
# app/lender_auth.py) with their own real Supabase Auth account — a
# separate cookie pair means a borrower session and a lender session can
# coexist in the same browser without one clobbering the other, which a
# developer testing both apps at once will do routinely.
COOKIE_LENDER_ACCESS = "sw_lender_access_token"
COOKIE_LENDER_REFRESH = "sw_lender_refresh_token"
ACCESS_MAX_AGE = 60 * 60  # Supabase access tokens default to 1h
REFRESH_MAX_AGE = 60 * 60 * 24 * 30  # refresh tokens are long-lived


@dataclass
class AuthedUser:
    id: str
    email: str | None


def _cookie_kwargs(max_age: int) -> dict:
    return {
        "httponly": True,
        "secure": COOKIE_SECURE,
        "samesite": COOKIE_SAMESITE,
        "max_age": max_age,
        "path": "/",
    }


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    *,
    access_cookie: str = COOKIE_ACCESS,
    refresh_cookie: str = COOKIE_REFRESH,
) -> None:
    response.set_cookie(access_cookie, access_token, **_cookie_kwargs(ACCESS_MAX_AGE))
    response.set_cookie(refresh_cookie, refresh_token, **_cookie_kwargs(REFRESH_MAX_AGE))


def clear_auth_cookies(
    response: Response, *, access_cookie: str = COOKIE_ACCESS, refresh_cookie: str = COOKIE_REFRESH
) -> None:
    response.delete_cookie(access_cookie, path="/")
    response.delete_cookie(refresh_cookie, path="/")


def verify_access_token(token: str) -> AuthedUser:
    """Raises jwt.PyJWTError (including ExpiredSignatureError) on failure."""
    signing_key = _jwk_client.get_signing_key_from_jwt(token)
    claims = jwt.decode(token, signing_key.key, algorithms=["ES256", "RS256"], audience="authenticated")
    return AuthedUser(id=claims["sub"], email=claims.get("email"))


async def _refresh(refresh_token: str) -> dict:
    """Calls Supabase's refresh endpoint. Raises on failure (expired/revoked
    refresh token — the caller should treat that as a full 401)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "refresh_token"},
            json={"refresh_token": refresh_token},
            headers={"apikey": SUPABASE_ANON_KEY},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Session expired")
    return resp.json()


async def password_grant(email: str, password: str) -> dict:
    """Supabase's password-grant login call — shared by the borrower and
    lender login endpoints (routers/auth.py, routers/lender_auth.py) so the
    same httpx call isn't duplicated and can't drift between the two."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "password"},
            json={"email": email, "password": password},
            headers={"apikey": SUPABASE_ANON_KEY},
        )
    if resp.status_code >= 400:
        # Generic message regardless of which part was wrong — avoids
        # confirming to an attacker whether an email is registered.
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return resp.json()


async def get_current_user(
    request: Request,
    response: Response,
    *,
    access_cookie: str = COOKIE_ACCESS,
    refresh_cookie: str = COOKIE_REFRESH,
) -> AuthedUser:
    access_token = request.cookies.get(access_cookie)
    refresh_token = request.cookies.get(refresh_cookie)

    if access_token:
        try:
            return verify_access_token(access_token)
        except jwt.ExpiredSignatureError:
            pass
        except jwt.PyJWTError as e:
            raise HTTPException(status_code=401, detail="Invalid session") from e

    if not refresh_token:
        raise HTTPException(status_code=401, detail="Not signed in")

    session = await _refresh(refresh_token)
    set_auth_cookies(
        response, session["access_token"], session["refresh_token"], access_cookie=access_cookie, refresh_cookie=refresh_cookie
    )
    return verify_access_token(session["access_token"])


async def get_current_user_optional(request: Request, response: Response) -> AuthedUser | None:
    try:
        return await get_current_user(request, response)
    except HTTPException:
        return None
