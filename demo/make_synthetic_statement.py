"""Generates a fully synthetic M-Pesa statement for demos.

No real data goes in: the persona, phone number, receipt codes, counterparties
and amounts are all generated here from a fixed seed. Layout follows what
pesascore-backend/app/pdf_parser.py expects from real Safaricom exports:
receipt + timestamp at line start, details that can wrap, then
Completed/Failed, one signed amount, and the balance.

Run with the backend venv:
    ..\\venv\\Scripts\\python.exe make_synthetic_statement.py
"""

import random
import string
from datetime import datetime, timedelta

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

SEED = 20260924
NAME = "JANE DEMO WANJIKU"
PHONE = "254700000000"
START = datetime(2026, 3, 1)
END = datetime(2026, 8, 31)
PASSWORD = "1234"

rng = random.Random(SEED)


def receipt(dt: datetime) -> str:
    return "T" + "".join(rng.choices(string.ascii_uppercase + string.digits, k=9))


MERCHANTS = [
    "Buy Goods from 5123401 - MAMA NJERI GROCERS",
    "Buy Goods from 5123402 - KILIMANI CHEMIST",
    "Buy Goods from 5123403 - TUSKER FRESH MART",
    "Buy Goods from 5123404 - SIMBA FUEL STATION",
    "Buy Goods from 5123405 - UPPER HILL CAFE",
    "Buy Goods from 5123406 - JIKONI BUTCHERY",
    "Buy Goods from 5123407 - DEMO HARDWARE LTD",
    "Pay Bill to 888880 - KENYA POWER PREPAID Acc. 00000000001",
    "Pay Bill to 444400 - NAIROBI WATER DEMO Acc. 000123",
    "Pay Bill to 903470 - M-PESA GlobalPay Acc. STREAMING SUB",
]
PEOPLE = [
    "Customer Transfer to 2547******11 - PETER DEMO OTIENO",
    "Customer Transfer to 2547******22 - MARY DEMO ACHIENG",
    "Customer Transfer to 2547******33 - MUM DEMO WANJIKU",
]


def build_rows():
    rows = []  # (datetime, details, status, signed_amount)
    d = START
    while d <= END:
        # Salary: 28th of each month (slides to the 27th/29th sometimes).
        if d.day == 28:
            pay_day = d + timedelta(days=rng.choice([-1, 0, 0, 0, 1]))
            rows.append((pay_day.replace(hour=9, minute=rng.randint(0, 59)),
                         "Business Payment from 600100 - ACME DEMO LOGISTICS LTD via API. Original conversation ID is DEMO",
                         "Completed", 62000.0 + rng.choice([0, 0, 1500])))
        # SACCO loan repayment: 5th of each month, one failed attempt then retry in April.
        if d.day == 5:
            if d.month == 4:
                rows.append((d.replace(hour=8), "Pay Bill to 555111 - UMOJA DEMO SACCO Acc. LOAN 00042", "Failed", -8500.0))
                rows.append(((d + timedelta(days=2)).replace(hour=8), "Pay Bill to 555111 - UMOJA DEMO SACCO Acc. LOAN 00042", "Completed", -8500.0))
            else:
                rows.append((d.replace(hour=8), "Pay Bill to 555111 - UMOJA DEMO SACCO Acc. LOAN 00042", "Completed", -8500.0))
        # M-Shwari savings: 1st of each month, plus some weekly top-ups.
        if d.day == 1 or (d.weekday() == 5 and rng.random() < 0.5):
            amt = 6000.0 if d.day == 1 else float(rng.choice([500, 1000, 1500]))
            rows.append((d.replace(hour=19), "M-Shwari Deposit", "Completed", -amt))
        # Bank paybill: shows up in the ambiguous review step (loan or not?).
        if d.day == 15:
            rows.append((d.replace(hour=12), "Pay Bill to 400000 - DEMO BANK Money Transfer Acc. 0000111", "Completed", -4000.0))
        # Fuliza: mostly the week before payday, heavier in the first months.
        fuliza_p = 0.35 if d.month <= 5 else 0.18
        if 20 <= d.day <= 27 and rng.random() < fuliza_p:
            code_t = d.replace(hour=rng.randint(10, 20), minute=rng.randint(0, 59))
            amt = float(rng.choice([300, 500, 800, 1200]))
            rows.append((code_t, "OverDraft of Credit Party", "Completed", amt))
            rows.append((code_t, "Buy Goods from 5123403 - TUSKER FRESH MART via Fuliza", "Completed", -amt))
        if d.day == 29 and d.month <= 7:
            rows.append((d.replace(hour=10), "OD Loan Repayment to 232323 - M-PESA Fuliza", "Completed", -float(rng.choice([900, 1500, 2100]))))
        # Everyday spending and transfers.
        for _ in range(rng.choice([0, 1, 1, 2, 2, 3])):
            rows.append((d.replace(hour=rng.randint(7, 21), minute=rng.randint(0, 59)),
                         rng.choice(MERCHANTS), "Completed", -float(rng.randint(2, 40) * 50)))
        if rng.random() < 0.25:
            rows.append((d.replace(hour=rng.randint(8, 21)), rng.choice(PEOPLE), "Completed", -float(rng.randint(5, 60) * 50)))
        if rng.random() < 0.08:
            rows.append((d.replace(hour=rng.randint(8, 21)),
                         "Funds received from 2547******44 - SIDE GIG DEMO CLIENT", "Completed", float(rng.randint(10, 80) * 100)))
        d += timedelta(days=1)

    rows.sort(key=lambda r: r[0])
    balance = 15000.0
    out = []
    for dt, details, status, amt in rows:
        if status == "Completed":
            balance += amt
        out.append((receipt(dt), dt, details, status, amt, balance))
    return out


def fmt(x: float) -> str:
    return f"{x:,.2f}"


def draw(path: str, rows, encrypt: str | None):
    c = canvas.Canvas(path, pagesize=A4, encrypt=encrypt)
    w, h = A4

    def header():
        c.setFont("Helvetica-Bold", 13)
        c.drawString(40, h - 45, "M-PESA STATEMENT")
        c.setFont("Helvetica", 8.5)
        c.drawString(40, h - 62, f"Customer Name: {NAME}")
        c.drawString(40, h - 74, f"Mobile Number: {PHONE}")
        c.drawString(40, h - 86, f"Statement Period: {START:%d %b %Y} - {END:%d %b %Y}")
        c.setFillColorRGB(0.75, 0.1, 0.1)
        c.drawString(330, h - 62, "SYNTHETIC DEMO DATA - NOT A REAL CUSTOMER")
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(40, h - 108, "Receipt No.   Completion Time        Details                                                        Status        Paid In / Withdrawn   Balance")

    header()
    y = h - 124
    c.setFont("Helvetica", 7.5)
    for code, dt, details, status, amt, bal in rows:
        # Wrap long details across two physical lines like real exports do.
        first, rest = details[:60], details[60:]
        needed = 2 if rest else 1
        if y - needed * 11 < 40:
            c.showPage()
            header()
            y = h - 124
            c.setFont("Helvetica", 7.5)
        if rest:
            c.drawString(40, y, f"{code}  {dt:%Y-%m-%d %H:%M:%S}  {first}")
            y -= 11
            c.drawString(40, y, f"{rest}  {status}  {fmt(amt)}  {fmt(bal)}")
        else:
            c.drawString(40, y, f"{code}  {dt:%Y-%m-%d %H:%M:%S}  {details}  {status}  {fmt(amt)}  {fmt(bal)}")
        y -= 13
    c.save()


if __name__ == "__main__":
    rows = build_rows()
    draw("PesaScore_DEMO_synthetic_statement.pdf", rows, None)
    draw("PesaScore_DEMO_synthetic_statement_pw1234.pdf", rows, PASSWORD)
    print(f"{len(rows)} synthetic transactions written")
