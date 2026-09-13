"""Seeds demo lenders + demo borrowers with real, varied period_metrics and
real grants linking them — so the lender dashboard has more than one
borrower's data to aggregate. Run manually:

    .\\venv\\Scripts\\python.exe scripts\\seed_demo_tenants.py

Not imported by app/ — a one-off operational script, not application code.

Uses the Supabase Admin API (SUPABASE_SERVICE_ROLE_KEY) to create the demo
auth.users directly — the first real use of that key, which .env.example
already reserves for exactly this. Idempotent: safe to rerun (looks up
existing users/rows by email/id before creating).

Every score shown by the demo dashboard is a REAL compute_score() output
over these seeded-but-labeled-as-seeded metrics, not a fabricated number.
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict

import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

load_dotenv()

from app.db import close_pool, db_conn_service, open_pool  # noqa: E402
from app.scoring import FulizaMetrics, PeriodMetrics, RepaymentMetrics, SavingsMetrics  # noqa: E402
from app.consent_categories import STANDARD_WILL_SHARE  # noqa: E402

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
DAY_MS = 24 * 60 * 60 * 1000

DEMO_LENDERS = [
    {"email": "demo-lender-1@pesascore.test", "org_name": "Amani SACCO Underwriting"},
    {"email": "demo-lender-2@pesascore.test", "org_name": "Mwalimu SACCO"},
]

# (on_time, late, missed), (fuliza_days, period_days), (total_saved, total_income)
# for the CURRENT period — chosen for qualitative variety (excellent down to
# very weak), not to hit any exact target score. previous is derived below
# by nudging on_time down slightly, so every borrower has a small, real delta.
_DEMO_BORROWER_METRICS = [
    ((18, 0, 0), (5, 60), (12000, 40000)),
    ((14, 1, 0), (15, 60), (8000, 40000)),
    ((11, 2, 0), (20, 60), (6000, 40000)),
    ((9, 2, 1), (30, 60), (4000, 40000)),
    ((8, 3, 1), (40, 60), (2500, 40000)),
    ((6, 3, 3), (45, 60), (1000, 40000)),
    ((4, 2, 6), (50, 60), (500, 35000)),
    ((3, 1, 8), (55, 60), (200, 30000)),
]


def _current_and_previous(repay, fuliza, savings) -> tuple[PeriodMetrics, PeriodMetrics]:
    on_time, late, missed = repay
    fuliza_days, period_days = fuliza
    saved, income = savings
    current = PeriodMetrics(
        repayments=RepaymentMetrics(on_time=on_time, late=late, missed=missed),
        fuliza=FulizaMetrics(days_active=fuliza_days, period_days=period_days),
        savings=SavingsMetrics(total_saved=saved, total_income=income),
    )
    # A slightly worse previous period (one fewer on-time payment, one more
    # late) — enough to produce a real, non-zero delta without a second
    # hand-tuned metrics table.
    previous = PeriodMetrics(
        repayments=RepaymentMetrics(on_time=max(0, on_time - 1), late=late + 1, missed=missed),
        fuliza=FulizaMetrics(days_active=fuliza_days, period_days=period_days),
        savings=SavingsMetrics(total_saved=saved, total_income=income),
    )
    return current, previous


async def _find_or_create_auth_user(client: httpx.AsyncClient, email: str, password: str) -> str:
    headers = {"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"}

    resp = await client.post(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers=headers,
        json={"email": email, "password": password, "email_confirm": True},
    )
    if resp.status_code in (200, 201):
        return resp.json()["id"]

    # Already exists — look it up. GoTrue's admin list endpoint doesn't
    # support a documented email filter across versions, so page through
    # (fine at this volume — a handful of demo accounts) and match client-side.
    list_resp = await client.get(f"{SUPABASE_URL}/auth/v1/admin/users", headers=headers, params={"per_page": 200})
    list_resp.raise_for_status()
    for user in list_resp.json().get("users", []):
        if user.get("email", "").lower() == email.lower():
            return user["id"]

    raise RuntimeError(f"Could not create or find auth user for {email}: {resp.status_code} {resp.text}")


async def seed_lenders(client: httpx.AsyncClient) -> list[dict]:
    lenders = []
    for spec in DEMO_LENDERS:
        user_id = await _find_or_create_auth_user(client, spec["email"], "demo-password-not-for-real-use")
        async with db_conn_service() as conn:
            await conn.execute(
                "insert into lenders (id, org_name) values (%s, %s) on conflict (id) do update set org_name = excluded.org_name",
                (user_id, spec["org_name"]),
            )
        lenders.append({"id": user_id, **spec})
        print(f"lender ready: {spec['org_name']} ({spec['email']}) -> {user_id}")
    return lenders


async def seed_borrowers(client: httpx.AsyncClient, lenders: list[dict]) -> None:
    now_ms = int(time.time() * 1000)
    for i, (repay, fuliza, savings) in enumerate(_DEMO_BORROWER_METRICS):
        email = f"demo-borrower-{i + 1}@pesascore.test"
        user_id = await _find_or_create_auth_user(client, email, "demo-password-not-for-real-use")
        current, previous = _current_and_previous(repay, fuliza, savings)
        lender = lenders[i % len(lenders)]

        # created_at spread across the last ~90 days, across different
        # weekdays, so the dashboard's busiest-day histogram has real,
        # non-degenerate data rather than everything landing on one day.
        days_ago = (i * 11 + 3) % 90
        created_at_ms = now_ms - days_ago * DAY_MS

        async with db_conn_service() as conn:
            await conn.execute(
                "insert into profiles (id, email) values (%s, %s)"
                " on conflict (id) do update set email = coalesce(profiles.email, excluded.email)",
                (user_id, email),
            )
            for period, metrics in (("current", current), ("previous", previous)):
                d = asdict(metrics)
                await conn.execute(
                    """
                    insert into period_metrics (user_id, period, repayments, fuliza, savings)
                    values (%s, %s, %s, %s, %s)
                    on conflict (user_id, period) do update set
                      repayments = excluded.repayments, fuliza = excluded.fuliza, savings = excluded.savings, updated_at = now()
                    """,
                    (user_id, period, json.dumps(d["repayments"]), json.dumps(d["fuliza"]), json.dumps(d["savings"])),
                )

            existing = await (await conn.execute(
                "select id from grants_table where user_id = %s and lender_id = %s", (user_id, lender["id"])
            )).fetchone()
            if existing is None:
                await conn.execute(
                    """
                    insert into grants_table (user_id, lender_id, lender_name, expires_at, will_share, created_at)
                    values (%s, %s, %s, %s, %s, to_timestamp(%s / 1000.0))
                    """,
                    (
                        user_id,
                        lender["id"],
                        lender["org_name"],
                        now_ms + 365 * DAY_MS,  # comfortably future so the demo stays live
                        json.dumps(STANDARD_WILL_SHARE),
                        created_at_ms,
                    ),
                )
        print(f"borrower ready: {email} -> {user_id}, granted to {lender['org_name']}")


async def main() -> None:
    await open_pool()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            lenders = await seed_lenders(client)
            await seed_borrowers(client, lenders)
    finally:
        await close_pool()
    print("done — safe to rerun.")


if __name__ == "__main__":
    asyncio.run(main())
