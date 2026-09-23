"""Lender principal dependencies. Reuses app/auth.py's JWT-verification and
cookie-refresh mechanism as-is (it only ever inspects a JWT's sub/email/aud —
it never assumes "authenticated = borrower") via the lender cookie pair, then
layers a DB lookup on top to answer "is this authenticated principal actually
a registered lender" — see the project plan for why a DB lookup was chosen
over a custom JWT claim (a claim set via the Admin API after signup wouldn't
be present on the very first login's access token).
"""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response

from .auth import COOKIE_LENDER_ACCESS, COOKIE_LENDER_REFRESH, AuthedUser, get_current_user
from .db import db_conn


async def get_current_lender_principal(request: Request, response: Response) -> AuthedUser:
    return await get_current_user(request, response, access_cookie=COOKIE_LENDER_ACCESS, refresh_cookie=COOKIE_LENDER_REFRESH, allow_bearer=False)


@dataclass
class AuthedLender:
    id: str
    email: str | None
    org_name: str


async def get_current_lender(user: AuthedUser = Depends(get_current_lender_principal)) -> AuthedLender:
    async with db_conn(user.id) as conn:
        row = await (await conn.execute("select org_name from lenders where id = %s", (user.id,))).fetchone()
    if row is None:
        raise HTTPException(status_code=403, detail="Not a registered lender account")
    return AuthedLender(id=user.id, email=user.email, org_name=row["org_name"])


async def get_current_lender_optional(request: Request, response: Response) -> AuthedLender | None:
    try:
        user = await get_current_lender_principal(request, response)
    except HTTPException:
        return None
    async with db_conn(user.id) as conn:
        row = await (await conn.execute("select org_name from lenders where id = %s", (user.id,))).fetchone()
    if row is None:
        return None
    return AuthedLender(id=user.id, email=user.email, org_name=row["org_name"])
