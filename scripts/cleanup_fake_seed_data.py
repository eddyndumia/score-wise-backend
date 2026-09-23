"""Removes the made-up data that accounts created before 2026-09-23 were seeded
with, plus throwaway test accounts. Dry run by default:

    .\\venv\\Scripts\\python.exe scripts\\cleanup_fake_seed_data.py           # show what would go
    .\\venv\\Scripts\\python.exe scripts\\cleanup_fake_seed_data.py --apply   # back up, then delete

What counts as fake (see docs/REQUIREMENTS.md, B14):
- grants_table / pending_consents rows with no lender_id: the seeded
  "Amani SACCO" access and requests, and old /v1/consent/simulate rows. No
  real lender can be behind a row without a lender_id.
- period_metrics for accounts whose current AND previous metrics are exactly
  the old seeded defaults, i.e. they never finished an upload. Their "score"
  was made up. Lenders stop seeing them (routers/lender.py skips no-score grants).
- cash_flow rows still holding the made-up "Week 1..8" series.
- consent_request notifications that don't match any real lender's request.
- Throwaway accounts made by test runs (flowtest-/rehearsal-/mobile-live-
  <digits>@pesascore.test), deleted entirely via the Supabase admin API;
  every table cascades from auth.users.

Never touched: grants and requests from real lenders, statement-based
scores, the demo tenants from seed_demo_tenants.py, lender accounts.
--apply writes every row it deletes to a timestamped JSON backup first.
"""

import argparse
import json
import os
import sys
import time

import httpx
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

_CURRENT_DEFAULT = (
    """repayments = '{"on_time":11,"late":1,"missed":0}'::jsonb"""
    """ and fuliza = '{"days_active":27,"period_days":60}'::jsonb"""
    """ and savings = '{"total_saved":9200,"total_income":46000}'::jsonb"""
)
_PREVIOUS_DEFAULT = (
    """repayments = '{"on_time":9,"late":2,"missed":1}'::jsonb"""
    """ and fuliza = '{"days_active":34,"period_days":60}'::jsonb"""
    """ and savings = '{"total_saved":6100,"total_income":44000}'::jsonb"""
)

TARGETS = {
    "grants_table": "lender_id is null",
    "pending_consents": "lender_id is null",
    "period_metrics": (
        f"user_id in (select user_id from period_metrics where period = 'current' and {_CURRENT_DEFAULT}"
        f" intersect select user_id from period_metrics where period = 'previous' and {_PREVIOUS_DEFAULT})"
    ),
    "cash_flow": "series->0->>'label' = 'Week 1'",
    "notifications": (
        "kind = 'consent_request' and not exists (select 1 from pending_consents c"
        " where c.user_id = notifications.user_id and c.lender_id is not null"
        " and notifications.message like c.lender_name || '%')"
    ),
}
TEST_ACCOUNT_RE = r"^(flowtest|rehearsal|mobile-live)-[0-9]+@pesascore\.test$"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="actually delete (after writing a backup)")
    args = parser.parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as db:
        # Notifications are matched against pending_consents, so read
        # everything before deleting anything.
        found = {t: db.execute(f"select * from {t} where {w}").fetchall() for t, w in TARGETS.items()}
        test_users = db.execute("select id, email from profiles where email ~ %s", (TEST_ACCOUNT_RE,)).fetchall()

        for table, rows in found.items():
            print(f"{table}: {len(rows)} fake row(s)")
        print(f"test accounts: {', '.join(u['email'] for u in test_users) or 'none'}")

        if not args.apply:
            print("\nDry run. Nothing deleted. Re-run with --apply to delete.")
            return

        backup = f"cleanup_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
        with open(backup, "w", encoding="utf-8") as f:
            json.dump({**found, "deleted_test_accounts": test_users}, f, default=str, indent=1)
        print(f"\nBackup written to {os.path.abspath(backup)}")

        with db.transaction():
            for table, where in TARGETS.items():
                print(f"deleted {db.execute(f'delete from {table} where {where}').rowcount} from {table}")

    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    for u in test_users:
        r = httpx.delete(
            f"{os.environ['SUPABASE_URL']}/auth/v1/admin/users/{u['id']}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=30,
        )
        print(f"deleted account {u['email']}: HTTP {r.status_code}")
        if r.status_code >= 300:
            sys.exit(1)


if __name__ == "__main__":
    main()
