# PesaScore Backend

The API behind [PesaScore](https://github.com/eddyndumia/scorewise). Parses real M-Pesa statement PDFs, scores them, and handles the consent that decides which lenders see a borrower's score.

FastAPI, Supabase auth, Postgres with Row-Level Security.

```bash
cp .env.example .env    # Supabase keys
pip install -r requirements.txt
python run.py
```

Use `run.py` rather than calling uvicorn directly. On Windows it sets the event loop the Postgres driver needs.
