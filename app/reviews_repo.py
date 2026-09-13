"""Load/save for pending_reviews (ambiguous-statement classification sessions
awaiting the user's yes/no answers), used by routers/statements.py. Moved out
of an in-process dict (app/store.py used to hold this) into Postgres so a
session survives a Render restart/idle-spindown instead of vanishing.

Lazily expired on read rather than swept by a cron: at this volume, checking
expires_at on the one read this session ever gets is enough.
"""

import json

REVIEW_TTL_HOURS = 24


async def save_pending_review(conn, user_id: str, session_id: str, rows: list[dict]) -> None:
    await conn.execute(
        """
        insert into pending_reviews (session_id, user_id, rows, expires_at)
        values (%s, %s, %s, now() + make_interval(hours => %s))
        """,
        (session_id, user_id, json.dumps(rows), REVIEW_TTL_HOURS),
    )


async def load_pending_review(conn, user_id: str, session_id: str) -> list[dict] | None:
    """Returns the session's rows, or None if missing, expired, or owned by a
    different account. Opportunistically deletes an expired row it finds
    rather than leaving it for a future sweep."""
    row = await (await conn.execute(
        "select user_id, rows, expires_at < now() as is_expired from pending_reviews where session_id = %s",
        (session_id,),
    )).fetchone()
    # row["user_id"] comes back as a uuid.UUID (psycopg's native mapping for
    # a uuid column); user_id is the plain string from the JWT's `sub` claim.
    # UUID.__eq__ never equals a str, so this must compare via str() on both
    # sides — comparing them directly always looks like a different owner
    # and silently 404s every legitimate load.
    if row is None or str(row["user_id"]) != user_id:
        return None
    if row["is_expired"]:
        await conn.execute("delete from pending_reviews where session_id = %s", (session_id,))
        return None
    return row["rows"]


async def delete_pending_review(conn, session_id: str) -> None:
    await conn.execute("delete from pending_reviews where session_id = %s", (session_id,))
