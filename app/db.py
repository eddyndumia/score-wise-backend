"""Direct Postgres access to Supabase, RLS-scoped per request.

DATABASE_URL connects as the `postgres` superuser (via the session pooler,
since the direct db.<ref>.supabase.co host is IPv6-only and this network has
no IPv6 route). Superuser connections bypass Row Level Security entirely by
default — so every per-user operation must explicitly drop privileges inside
its own transaction with SET LOCAL ROLE authenticated before running any
query. That's what makes RLS the actual security boundary here, not an
application-level `WHERE user_id = ...` check (see supabase/schema.sql).

Session-mode pooler (not transaction-mode) is required for this pattern:
transaction-mode pgbouncer can silently drop SET LOCAL state or fight with
prepared statements across pooled transactions.
"""

import json
import os
from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.environ["DATABASE_URL"]

# Small pool: this is a single dev/demo backend process, not a fleet of
# workers. psycopg_pool opens connections lazily up to max_size.
pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=False, kwargs={"row_factory": dict_row})


async def open_pool() -> None:
    await pool.open(wait=True)


async def close_pool() -> None:
    await pool.close()


@asynccontextmanager
async def db_conn(user_id: str):
    """Yields a connection inside a transaction scoped to `user_id` via RLS.
    Commits on success, rolls back on any exception."""
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', %s, true)",
                (json.dumps({"sub": user_id, "role": "authenticated"}),),
            )
            yield conn


@asynccontextmanager
async def db_conn_service():
    """Superuser connection, no RLS restriction. Only for operations that are
    not scoped to a single user (none yet in this pass — reserved for future
    admin tooling, matching the backend CLAUDE.md's service-role plan)."""
    async with pool.connection() as conn:
        async with conn.transaction():
            yield conn
