"""One-off script to generate a synthetic but realistically-formatted M-Pesa
statement PDF for testing pdf_parser.py. Not part of the app."""

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

rows = [
    # receipt, date, time, details, status, paid_in, withdrawn, balance
    ("QAG7H8J2K1", "2024-01-03", "09:12:04", "Customer Transfer to JOHN DOE 254712345678", "Completed", "0.00", "1,500.00", "18,500.00"),
    ("QAG7H8J2K2", "2024-01-05", "14:23:11", "Pay Bill to AMANI SACCO LOAN REPAYMENT", "Completed", "0.00", "3,000.00", "15,500.00"),
    ("QAG7H8J2K3", "2024-01-08", "08:05:33", "Fuliza M-Pesa Overdraft Access", "Completed", "500.00", "0.00", "16,000.00"),
    ("QAG7H8J2K4", "2024-01-09", "20:10:02", "Fuliza M-Pesa Repayment", "Completed", "0.00", "520.00", "15,480.00"),
    ("QAG7H8J2K5", "2024-01-12", "11:45:00", "Funds received from EMPLOYER LTD", "Completed", "45,000.00", "0.00", "60,480.00"),
    ("QAG7H8J2K6", "2024-01-15", "16:30:45", "Customer Transfer to M-Shwari Lock Savings", "Completed", "0.00", "5,000.00", "55,480.00"),
    ("QAG7H8J2K7", "2024-01-18", "07:55:19", "Pay Bill to AMANI SACCO LOAN REPAYMENT", "Completed", "0.00", "3,000.00", "52,480.00"),
    ("QAG7H8J2K8", "2024-01-22", "19:02:57", "Fuliza M-Pesa Overdraft Access", "Completed", "800.00", "0.00", "53,280.00"),
    ("QAG7H8J2K9", "2024-01-25", "13:14:29", "Pay Bill to AMANI SACCO LOAN REPAYMENT", "Failed", "0.00", "0.00", "53,280.00"),
    ("QAG7H8J2KA", "2024-01-29", "10:00:00", "Customer Transfer to M-Shwari Lock Savings", "Completed", "0.00", "4,200.00", "49,080.00"),
    ("QAG7H8J2KB", "2024-02-02", "09:12:04", "Customer Transfer to JOHN DOE 254712345678", "Completed", "0.00", "1,200.00", "47,880.00"),
    ("QAG7H8J2KC", "2024-02-05", "14:23:11", "Pay Bill to AMANI SACCO LOAN REPAYMENT", "Completed", "0.00", "3,000.00", "44,880.00"),
    ("QAG7H8J2KD", "2024-02-10", "11:45:00", "Funds received from EMPLOYER LTD", "Completed", "45,000.00", "0.00", "89,880.00"),
    ("QAG7H8J2KE", "2024-02-14", "16:30:45", "Customer Transfer to M-Shwari Lock Savings", "Completed", "0.00", "6,000.00", "83,880.00"),
    ("QAG7H8J2KF", "2024-02-19", "07:55:19", "Pay Bill to AMANI SACCO LOAN REPAYMENT", "Completed", "0.00", "3,000.00", "80,880.00"),
]

c = canvas.Canvas("mpesa-statement-test.pdf", pagesize=A4)
c.setFont("Helvetica", 8)
y = 800
c.drawString(40, y, "M-PESA STATEMENT")
y -= 30
for receipt, date, time, details, status, paid_in, withdrawn, balance in rows:
    line = f"{receipt}  {date} {time}  {details}  {status}  {paid_in}  {withdrawn}  {balance}"
    c.drawString(40, y, line)
    y -= 14
c.save()
print("wrote mpesa-statement-test.pdf")
