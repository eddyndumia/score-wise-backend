"""Runs a statement through the REAL backend parser and scorer and prints each
stage. This is the offline stand-in for the parts the UI doesn't show
(parsed rows, tags, raw features). Nothing here is precomputed.

    ..\\venv\\Scripts\\python.exe show_signals.py PesaScore_DEMO_synthetic_statement.pdf [password]

Answers every ambiguous bank-paybill group as "not a repayment" (same as a
user tapping No on the review screen). Pass --yes to answer Yes instead.
"""

import os
import statistics
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.pdf_parser import (  # noqa: E402
    classify_rows,
    extract_account_name,
    extract_text,
    parse_transactions,
    split_and_compute,
)
from app.scoring import compute_score  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    answer_yes = "--yes" in sys.argv
    path = args[0]
    password = args[1] if len(args) > 1 else None

    text = extract_text(open(path, "rb").read(), password)
    print(f"\n1. PARSE   customer name on statement: {extract_account_name(text)}")
    rows = parse_transactions(text)
    print(f"           {len(rows)} transactions parsed, {rows[0]['date']} to {rows[-1]['date']}")
    for r in rows[:6]:
        print(f"           {r['date']}  {r['status']:<9} {r['amount']:>11,.2f}  {r['details'][:55]}")

    tagged, groups = classify_rows(rows)
    print("\n2. CLASSIFY  tags:", dict(Counter(r["tag"] for r in tagged)))
    for g in groups:
        print(f"           ambiguous -> asks user: {g['businessName']} ({g['count']}x, KES {g['totalAmount']:,.0f})")

    overrides = {g["id"]: answer_yes for g in groups}
    previous, current = split_and_compute(tagged, overrides)
    print("\n3. FEATURES (current half of statement)")
    print(f"           repayments on-time/late/missed: {current.repayments.on_time}/{current.repayments.late}/{current.repayments.missed}")
    print(f"           Fuliza-active days: {current.fuliza.days_active} of {current.fuliza.period_days}")
    print(f"           saved KES {current.savings.total_saved:,.0f} of income KES {current.savings.total_income:,.0f}")

    result = compute_score(current, previous)
    print(f"\n4. SCORE   {result.score}  (range 300-850, previous half {result.previous_score}, delta {result.delta:+d})")
    for s in result.signals:
        print(f"           {s.label:<18} {s.value:>3}  {s.status:<8}  {s.explanation}")

    # Exploratory only: computed here, NOT part of the score yet.
    real = [r for r in tagged if r["tag"] != "internal" and r["status"] == "Completed"]
    salary_days = sorted({r["date"] for r in real if r["amount"] >= 20000})
    gaps = [(datetime.fromisoformat(b) - datetime.fromisoformat(a)).days for a, b in zip(salary_days, salary_days[1:])]
    merchants = {r["details"].split(" - ")[-1][:30] for r in real if r["details"].startswith("Buy Goods")}
    print("\n5. EXPLORATORY (not in the score yet)")
    if gaps:
        print(f"           large inflows: {len(salary_days)}, avg gap {statistics.mean(gaps):.1f} days, spread {statistics.pstdev(gaps):.1f} days")
    print(f"           distinct Buy Goods merchants: {len(merchants)}")
    print()


if __name__ == "__main__":
    main()
