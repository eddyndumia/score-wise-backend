# PesaScore requirements

Source: the public post "Your M-Pesa Statement Already Knows If You'll Pay Back"
(portfolio, 2026-09-23). Everything the post promises a borrower or a lender is
a requirement. If the product and the post disagree, the product is wrong, or
the post gets corrected, never quietly left different.

Status key: **done** (built and checked against the running system), **partial**,
**open**.

## Borrower (mobile app)

| # | Requirement (from the post) | Status |
|---|---|---|
| B1 | Guide the borrower to download their statement from the M-PESA app or USSD, the same way they would for a loan application | done in the web app (StatementInstructions); mobile: open |
| B2 | Upload the statement PDF; if it's password protected, type the password | done (backend + web); mobile: open |
| B3 | The PDF is read on the server, in memory, and thrown away. Never saved | done. Also: the 24h review session now stores only date/amount/status/tag per row, no descriptions or counterparties (fixed 2026-09-23) |
| B4 | Sort every transaction into repayments, Fuliza, savings, everything else | done (pdf_parser.classify_rows) |
| B5 | Where it can't tell, ask. Bank paybills are shown by bank and the borrower answers whether they were loan repayments | done (needs_review + classify) |
| B6 | Score between 300 and 850 with the three things that built it: repayment reliability, Fuliza reliance, savings vs income | done |
| B7 | Each signal comes with a plain explanation of why it's there and what would move it | done (explanation + recommendation per signal) |
| B8 | Simulator: e.g. cut Fuliza by ten days, save an extra 5,000 a month | done |
| B9 | A lender's request arrives on the phone saying who is asking and exactly what they'll see | partial: request + in-app notification exist; no push notification yet |
| B10 | Borrower says yes or no | done |
| B11 | On yes, the lender sees the score and only the parts agreed to, for a set time | done (will_share filtering + expiry) |
| B12 | Borrower can pull access back whenever they want | done (revoke) |
| B13 | The borrower sees the score first. Nothing can be shared before they have one | done: approval returns 409 no_score until a statement is uploaded (fixed 2026-09-23) |
| B14 | No made-up numbers. A score only ever comes from the borrower's own statement | done: removed the fake default score, cash flow, consent request and pre-granted lender access new accounts used to get (fixed 2026-09-23) |
| B15 | No scraping. Only the statement the borrower chooses to upload | done |

## Lender (web portal)

| # | Requirement | Status |
|---|---|---|
| L1 | A lender requests a borrower's score | done |
| L2 | Lender never sees transactions | done |
| L3 | Each lender only ever sees their own applicants, enforced in the database, not just hidden in the app | done (Postgres RLS) |
| L4 | Lender only sees a real, statement-based score | done: grants for borrowers with no score are skipped (fixed 2026-09-23) |

## Production requirements (from "what isn't done" in the post)

| # | Requirement | Status |
|---|---|---|
| P1 | Validate weights against real loan outcomes with a lender partner | open |
| P2 | Parser tested on many more real statements, including aggregator-routed loans | open |
| P3 | Automated test suite | open |
| P4 | Data Protection Act: register with the ODPC, DPIA, consent/access audit log | open (no audit log table yet) |
| P5 | Work out where a scoring service sits under the CRB regulations | open |
| P6 | PIN / device lock stored securely, not in plain browser storage | open in web; mobile uses the OS keystore |

## Mobile auth

The mobile app uses bearer tokens (`/v1/auth/token*`), kept in the OS keystore.
The web apps keep httpOnly cookies. Lenders are web-only: a bearer token is
never accepted on a lender route.
