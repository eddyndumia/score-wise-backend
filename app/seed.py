"""Seeds a fresh account with the same defaults the old in-memory Store used
to initialize with — called once at signup, and again by the soft "Reset
account" path (see routers/account.py), so both start from an identical
known state.
"""

import json
import time
from dataclasses import asdict

from . import store


async def seed_new_account(conn, user_id: str) -> None:
    await conn.execute(
        """
        insert into profiles (id) values (%s)
        on conflict (id) do nothing
        """,
        (user_id,),
    )

    current = asdict(store.DEFAULT_CURRENT_METRICS)
    previous = asdict(store.DEFAULT_PREVIOUS_METRICS)
    for period, metrics in (("current", current), ("previous", previous)):
        await conn.execute(
            """
            insert into period_metrics (user_id, period, repayments, fuliza, savings)
            values (%s, %s, %s, %s, %s)
            on conflict (user_id, period) do nothing
            """,
            (user_id, period, json.dumps(metrics["repayments"]), json.dumps(metrics["fuliza"]), json.dumps(metrics["savings"])),
        )

    await conn.execute(
        """
        insert into cash_flow (user_id, series) values (%s, %s)
        on conflict (user_id) do nothing
        """,
        (user_id, json.dumps(store.DEFAULT_CASH_FLOW)),
    )

    await conn.execute(
        """
        insert into pending_consents (user_id, lender_name, grant_duration_days, will_share, wont_share)
        values (%s, %s, %s, %s, %s)
        """,
        (
            user_id,
            store.DEFAULT_PENDING_CONSENT["lender_name"],
            store.DEFAULT_PENDING_CONSENT["grant_duration_days"],
            json.dumps(store.DEFAULT_PENDING_CONSENT["will_share"]),
            json.dumps(store.DEFAULT_PENDING_CONSENT["wont_share"]),
        ),
    )

    now_ms = int(time.time() * 1000)
    await conn.execute(
        """
        insert into grants_table (user_id, lender_name, expires_at)
        values (%s, %s, %s)
        """,
        (user_id, store.DEFAULT_GRANT_LENDER_NAME, now_ms + store.DEFAULT_GRANT_DURATION_MS),
    )
