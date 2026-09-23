# PesaScore Backend

The API behind [PesaScore](https://github.com/eddyndumia/scorewise) and its [lender dashboard](https://github.com/eddyndumia/pesascore-lender). Parses real M-Pesa statement PDFs, scores them, and handles the consent that decides which lenders see a borrower's score.

FastAPI, Supabase auth, Postgres with Row-Level Security.

```bash
cp .env.example .env    # Supabase keys
pip install -r requirements.txt
python run.py
```

Use `run.py` rather than calling uvicorn directly. On Windows it sets the event loop the Postgres driver needs.

## Demo without real data

`demo/` has a fully synthetic M-Pesa statement (made-up person, numbers and receipts) in the same layout as a real Safaricom export, and a script that runs it through the real parser and scorer and prints each step.

```bash
cd demo
../venv/Scripts/python.exe make_synthetic_statement.py   # rebuilds the PDFs, password copy uses 1234
../venv/Scripts/python.exe show_signals.py PesaScore_DEMO_synthetic_statement.pdf
```

`show_signals.py` needs no database or internet. Upload the same PDF in the consumer app to see it end to end. Use a fresh account, since the first upload locks the account to the name on the statement.

## Where it stands

- The parser has been checked against one real two-year statement (about 7,400 transactions) plus the synthetic one.
- The score is a rules scorecard on a 300 to 850 scale, built from three signals: repayments, Fuliza reliance and savings. It is not a trained model and hasn't been validated against real loan outcomes yet.
- There are no automated tests yet.
