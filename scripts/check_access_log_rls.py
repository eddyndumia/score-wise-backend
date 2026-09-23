r"""Checks access_log's row-level security against the real database.

    .\venv\Scripts\python.exe scripts\check_access_log_rls.py

Every attempt runs in a transaction that is rolled back, so nothing is
written. Needs the demo lender from seed_demo_tenants.py. Every line should
start with OK.
"""
import os, json, psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv; load_dotenv(".env")

db = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True)
lender = db.execute("select id, org_name from lenders where org_name = 'Amani SACCO Underwriting'").fetchone()
granted = db.execute("select user_id from grants_table where lender_id = %s and expires_at > extract(epoch from now())*1000 limit 1", (lender["id"],)).fetchone()["user_id"]
stranger = db.execute("select p.id from profiles p where p.id not in (select user_id from grants_table where lender_id = %s) and p.id not in (select id from lenders) limit 1", (lender["id"],)).fetchone()["id"]

def attempt(label, as_user, sql, params=(), expect_ok=True):
    # Raising inside db.transaction() rolls it back, so every attempt,
    # allowed or not, leaves the database untouched.
    try:
        with db.transaction():
            db.execute("set local role authenticated")
            db.execute("select set_config('request.jwt.claims', %s, true)",
                       (json.dumps({"sub": str(as_user), "role": "authenticated"}),))
            cur = db.execute(sql, params)
            raise _Done(cur.rowcount)
    except _Done as d:
        print(("OK  " if expect_ok else "BAD ") + f"{label}: allowed ({d.args[0]} row(s)), rolled back")
    except psycopg.Error as e:
        print(("OK  " if not expect_ok else "BAD ") + f"{label}: refused ({type(e).__name__})")

class _Done(Exception): pass

ins = "insert into access_log (borrower_id, lender_id, lender_name, event) values (%s, %s, %s, %s) returning id"
attempt("borrower logs own approval", granted, ins, (granted, lender["id"], "x", "request_approved"))
attempt("borrower logs a fake score_viewed", granted, ins, (granted, lender["id"], "x", "score_viewed"), expect_ok=False)
attempt("borrower logs event about someone else", granted, ins, (stranger, lender["id"], "x", "request_approved"), expect_ok=False)
attempt("lender logs view of a borrower it has a grant for", lender["id"], ins, (granted, lender["id"], "x", "score_viewed"))
attempt("lender logs view of a borrower it has NO grant for", lender["id"], ins, (stranger, lender["id"], "x", "score_viewed"), expect_ok=False)
attempt("lender logs a view under another lender's id", lender["id"], ins, (granted, None, "x", "score_viewed"), expect_ok=False)
attempt("lender fakes a borrower approval", lender["id"], ins, (granted, lender["id"], "x", "request_approved"), expect_ok=False)
attempt("app role updates a log row", granted, "update access_log set event = 'request_denied'", (), expect_ok=False)
attempt("app role deletes log rows", granted, "delete from access_log", (), expect_ok=False)
