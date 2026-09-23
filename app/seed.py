"""Creates the rows a fresh account needs — called once at signup, and again
by the soft "Reset account" path (see routers/account.py), so both start from
the same state.

A new borrower gets a profile row and nothing else: no score, no cash flow,
no lender requests, no grants. All of those only ever come from the
borrower's own statement upload or a real lender's request.
"""


async def seed_new_account(conn, user_id: str, email: str | None = None) -> None:
    await conn.execute(
        """
        insert into profiles (id, email) values (%s, %s)
        on conflict (id) do update set email = coalesce(profiles.email, excluded.email)
        """,
        (user_id, email),
    )


async def seed_new_lender(conn, user_id: str, org_name: str) -> None:
    """Seeds a fresh lender account — a real org account, not a borrower.
    Never called from /v1/auth/signup; a separate lender signup endpoint
    (routers/lender_auth.py) calls this instead, so there's no branch on
    the existing borrower signup path at all."""
    await conn.execute(
        "insert into lenders (id, org_name) values (%s, %s) on conflict (id) do nothing",
        (user_id, org_name),
    )
