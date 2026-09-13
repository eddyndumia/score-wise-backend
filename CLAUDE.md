# ScoreWise — Backend

FastAPI service shared by both ScoreWise apps (`../scorewise-consumer`,
`../scorewise-lender`). Currently only the consumer app is wired up to it —
the lender app still runs on its own local mocks (see its CLAUDE.md).

## Status: real, working, real infra

Everything here actually does what it says — real PDF parsing, real scoring,
real state mutation, and (as of this pass) real Supabase Auth + a real
Postgres database with Row Level Security, replacing the old in-memory
singleton `Store`. See "Supabase migration, pass 1" below for the details,
gotchas, and what's still deliberately out of scope.

## Run it

```
.\venv\Scripts\python.exe run.py
```

**Use `run.py`, not `uvicorn app.main:app` directly** — Windows defaults
asyncio to `ProactorEventLoop`, which psycopg's async pool (`app/db.py`) can't
run under. `run.py` sets `WindowsSelectorEventLoopPolicy` before importing
uvicorn; setting it inside the `app` package itself is too late, since
`uvicorn.run()` creates its event loop *before* it imports the app string.
Requires `scorewise-backend/.env` — see `.env.example` and "Supabase
migration, pass 1" below for what each variable is and where to find it.

**Gotcha learned the hard way**: `--reload` watches this whole directory tree.
Creating or deleting *any* file here — including an ad-hoc debug script you
meant to throw away — triggers WatchFiles and restarts the worker, silently
wiping all in-memory state back to `store.py`'s defaults. This looked exactly
like a data bug (a registered profile name reverting to `null`) until traced
to the reload log. Put debug/one-off scripts in the OS temp dir instead,
reading this repo's files by absolute path — never inside `scorewise-backend/`
itself while the server is running.

CORS is currently locked to `http://localhost:5173` (the consumer app's dev
server) in `app/main.py`, now with `allow_credentials=True` since auth cookies
flow through it — add the lender app's origin there if it starts calling this
backend too, and never widen to a wildcard origin (incompatible with
credentialed CORS anyway, per the spec).

## Endpoints

Every endpoint below except the `/v1/auth/*` group requires a valid session
(the `sw_access_token` cookie) — see "Supabase migration, pass 1" for how
auth works and why it's cookie-based rather than a bearer token the frontend
handles itself.

- `POST /v1/auth/signup` / `POST /v1/auth/login` — `{email, password}`,
  proxies Supabase Auth, sets the session cookies on success. Signup also
  seeds the new account's default rows (`app/seed.py`).
- `POST /v1/auth/logout` — clears the session cookies.
- `GET /v1/auth/session` — `{authenticated, email?}`, used by the frontend on
  boot to decide where to route (see the consumer app's `lib/authSession.ts`).
- `GET /v1/score` — computes and returns the current score from whatever
  metrics are in this account's `period_metrics` rows (seeded defaults, or
  the result of the last statement upload).
- `GET /v1/consent` — the list of pending consent requests (a real queue,
  `store.pending_consents` — see "Multi-lender consent queue" below).
- `GET /v1/consent/{id}` — a single pending request by id; 404 if not found
  or already resolved.
- `POST /v1/consent/{id}/respond` — `{"approve": bool}`. Approving creates a
  real grant in the store; denying just removes the request from the queue.
  404 if the id isn't pending (already responded to, or never existed).
- `POST /v1/consent/simulate` — demo-only "incoming request" trigger (no real
  push mechanism from an actual lender exists). Adds a new pending request
  from a random name in a small fixed pool, avoiding a name already pending
  or already granted so repeated clicks don't look like duplicates from the
  same lender.
- `GET /v1/requests` — active grants.
- `POST /v1/requests/{id}/revoke` — removes a grant. 404 if it doesn't exist.
- `POST /v1/statements/upload` — multipart `file` (+ optional `password`
  field). Parses the PDF as an M-Pesa statement. The file is processed in
  memory and never written to disk. Two outcomes:
  - `{"status": "complete", ...score fields}` — no ambiguous transactions,
    score computed immediately (this is also the shape `GET /v1/score` returns).
  - `{"status": "needs_review", "sessionId": ..., "groups": [...]}` — some
    transactions couldn't be confidently classified (see "Ambiguous
    transaction review" below); the frontend must call the classify endpoint
    before a score exists for this upload.
  Can also 422 with `{"code": "name_mismatch", "expectedName", "foundName"}`
  if the statement's "Customer Name" doesn't match the account's registered
  name (see "Account name verification" below), or `{"code": "parse_error", ...}`
  for a bad PDF, wrong password, or no recognizable transactions.
- `POST /v1/statements/{sessionId}/classify` — body `{"answers": [{"groupId",
  "isRepayment"}, ...]}`, one entry per group from the `needs_review` response.
  Computes and returns the final score (`{"status": "complete", ...}`),
  consuming the session (a second call with the same id 404s).
- `GET /v1/profile` — `{"name": store.profile_name}` (null until either a
  statement registers it or the user sets it directly).
- `PUT /v1/profile` — body `{"name": str}`. Updates `store.profile_name` only
  — deliberately never touches `store.account_name`, which stays whatever the
  first statement's "Customer Name" said, since that's what later uploads are
  checked against. Renaming your profile must not weaken that check.
- `GET /v1/cash-flow` — `{"series": [{"label", "totalIn", "totalOut"}, ...]}`.
  Real statements get genuinely bucketed data (`pdf_parser.compute_cash_flow_series`
  — ~8 roughly-equal time windows across the whole statement, dated labels,
  summed from actual transaction amounts, excluding Fuliza's internal ledger
  legs since those aren't real cash flow). The no-statement default is a
  labeled-as-such demo series (`store.py`'s `_default_cash_flow`, "Week 1"
  etc., not real dates). Computed once at upload time regardless of whether
  ambiguous-group review is pending — whether a payment turns out to be "a
  loan repayment" doesn't change whether it was money leaving the account, so
  this doesn't need to wait on classify. Verified against the real statement:
  summed totals matched the frontend chart's displayed totals exactly (in:
  KES 2,817,357, out: KES 3,111,261).

## The PDF parser (`app/pdf_parser.py`) — now verified against a real statement

Originally built from the *documented* M-Pesa table format and only tested
against a synthetic PDF — that version matched **zero** transactions on the
first real statement it saw. Since fixed and re-verified against a real
password-protected Safaricom export (2-year period, ~7,400 transactions).
Two real-format facts the synthetic test fixture didn't reveal:

- "Details" text wraps across multiple physical lines before the
  Completed/Failed status appears — the parser now finds record boundaries
  (receipt code + timestamp) and slices between consecutive matches rather
  than assuming one line per transaction.
- Each row shows a single **signed amount + balance**, not separate Paid
  In/Withdrawn/Balance columns despite the header listing three — negative
  = money out, positive = money in. Confirmed against ~20 real examples.

`tests/generate_fixture.py` still generates a synthetic fixture (useful for
CI / quick smoke tests without a real statement on hand), but treat it as a
regression check, not proof the parser handles real statements — that claim
now rests on the real-statement test above, which isn't checked in (real
statements are personal financial data; don't commit one to this repo).

**Classification** (`REPAYMENT_KEYWORDS` etc.) — refined after checking real
data and a web search (Sep 2026) for actual Kenyan lender paybill numbers:
- SACCOs: matched by "sacco" in the business name — reliable, since SASRA's
  176 licensed deposit-taking societies are conventionally named "...SACCO...".
  Verified: 17/17 matches on the real statement were genuinely one SACCO.
- Named digital lenders + their paybill numbers as a fallback: Tala (851900),
  Branch (247988), Timiza/Absa (300067), plus Zenka/OKash/Mogo/Izwe by name.
  None of these appeared in the one real statement tested, so they're
  unverified against real data — only the SACCO path is.
- Deliberately **excludes** generic "pay bill" — a real statement showed
  M-Pesa's card-paybill number 903470 ("M-PESA GlobalPay") used for ordinary
  subscriptions (Netflix, Google, Coursera, etc.); a generic paybill match
  would have wrongly counted those as loan repayments. Also excludes generic
  bank-to-bank "Money Transfer" paybills (NCBA, Co-op, Absa) — too ambiguous
  to assume loan-related.
- Fuliza's own "OD Loan Repayment" / "OverDraft of Credit Party" internal
  ledger legs are excluded from repayment-history and income totals (they're
  not real external cash flow) but still correctly mark Fuliza-active days.
- Still an open gap: "Repayment history" can only measure *completed vs.
  failed* transactions matching these patterns — M-Pesa data alone can't show
  whether a payment was *late* relative to a due date, since that requires
  the lender's actual repayment schedule, which M-Pesa doesn't expose.
- A single statement is split at its date midpoint into a synthetic
  (previous, current) pair so `compute_score()` can still produce a delta —
  a real integration would compare against the borrower's actual prior
  statement instead.

## Ambiguous transaction review (human-in-the-loop classification)

Rather than silently guess on transactions the keyword rules can't confidently
classify, `classify_rows()` in `pdf_parser.py` puts them in a fourth bucket
("ambiguous") and groups them by counterparty for the user to confirm — see
`POST /v1/statements/upload` and `.../classify` above.

**What counts as "ambiguous" is deliberately narrow.** The first version
flagged any generic "Pay Bill"/"Business Payment" transaction not already
matched elsewhere — tested against the real statement, that produced **63**
distinct counterparties, including Spotify, KPLC, Jumia, a betting site, and
an airline. Nobody should be asked "is Kenya Airways a loan repayment?" It's
now restricted to counterparties whose business name contains a bank/finance
word (`BANK_OR_FINANCE_KEYWORDS` — "bank", "kcb", "equity", "co-operative",
"loan", "credit", "finance", "microfinance", "capital") **and** where the
amount is outgoing (a repayment is money leaving, not arriving). That cut the
real statement's ambiguous set from 63 to **9** genuine bank-paybill
counterparties (Equity, KCB, NCBA, Absa, Co-operative, Family Bank, IM Bank,
Consolidated Bank) — verified by hand that none of the excluded 54 were
plausibly loan-related, and by inspecting the 9 that all really are bank
paybills where a loan repayment is plausible.

This is a real, deliberate trade-off: it will miss loan repayments made
through banks/lenders whose business name doesn't contain any of those words,
and through payment aggregators (Cellulant, JamboPay, Paystack, Pesapal,
Tingg, SasaPay) that could theoretically carry a loan payment but were
excluded because in the one real statement tested they only ever carried
non-loan payments (Glovo, subscriptions, etc.) — treat that exclusion as
unverified for lenders who route through an aggregator instead of a direct
bank paybill.

Sessions live in a real `pending_reviews` Postgres table now (see
`app/reviews_repo.py`), not an in-memory dict — a restart no longer loses an
upload waiting on review answers. 24h TTL, lazily expired on read. See
"Supabase migration, pass 1" below for why this moved; the RLS-scoped
per-user_id design there carries over unchanged.

## Account name verification

`extract_account_name()` pulls the "Customer Name:" line from the statement
header. The **first** statement ever uploaded to a fresh store registers
`store.account_name`; every later upload must match it (case/whitespace-
insensitive exact match) or the request 422s with `name_mismatch` — see the
consumer app's `StatementUpload` screen for the resulting UI (a screen letting
the user go back or try a different file, not just an error string).

If a statement has no "Customer Name:" line at all (the synthetic test
fixtures don't), verification is skipped entirely rather than blocking the
upload — there's no way to check what isn't there, and refusing every
statement missing that exact header would be worse than not checking.

Known limitation: name matching is exact-normalized-string, not fuzzy. A
statement where Safaricom renders the name slightly differently (extra
middle name, different capitalization scheme, "Eddy N. Wamariu" vs "Eddy
Ndumia Wamariu") would incorrectly be flagged as a mismatch. Not fixed —
flagging this rather than guessing at a fuzzy-match threshold that could go
either way (too loose lets a different person's statement through; too tight
false-flags the legitimate owner).

## Scoring (`app/scoring.py`)

Direct port of `scorewise-consumer/frontend/src/lib/scoring.ts` — same
formulas, same thresholds. Verified numerically identical (same mock inputs
produce the same score, 746, on both sides). If one changes, change the
other — there's no code sharing between the TS and Python copies.

## Feature sprint: simulator, multi-lender queue, report export, savings
## goals, data export/deletion, notifications

Six of the seven features from this sprint (the 7th, email OTP, is blocked
on real Supabase credentials — see that project's CLAUDE.md) were built
against this in-memory backend rather than waiting for Supabase, since none
of them actually needed real per-user persistence to be genuinely functional
(they need it to be *multi-user*, which is a separate, already-tracked gap —
see "Security" above). All verified live via curl and the frontend, not just
written.

- **Score simulator** (`app/scoring.py`'s `apply_hypothetical`,
  `routers/score.py`'s `/v1/score/simulate` + `/v1/score/simulate/limits`) —
  a "what if" calculator: extra savings, fewer Fuliza days, fixed late/missed
  payments, recomputed through the *real* `compute_score`, not a separate
  guess. Verified: simulating +KES 5,000 savings, -10 Fuliza days, +1 on-time
  payment took the real 746 to 819 (+73), matching hand-checked math.
- **Multi-lender consent queue** (`store.pending_consents`, a list now
  instead of one fixed dict) — see the "Endpoints" section above. Verified
  live: simulate added a second pending request (KCB M-Pesa) alongside the
  seeded Amani SACCO one, approving one correctly moved it from "pending" to
  a real grant in `store.grants` while leaving the other queued.
- **Score report PDF** (`app/report.py`, `reportlab` — pure Python, no
  system dependency like weasyprint would need) — `GET /v1/score/report`.
  Downloadable, shareable with a lender/landlord who isn't integrated with
  ScoreWise. Carries the same liability disclaimer as the Terms & Conditions
  (not an official credit score, no lender guarantee). Verified by
  downloading and reading the actual PDF content, not just checking the
  HTTP status.
- **Savings goal** (`store.savings_goal`, `routers/savings_goal.py`) — target
  amount only; progress is read live off `current_metrics.savings.total_saved`
  at request time rather than duplicated into the goal record, so there's
  exactly one place "how much was saved" can be wrong. `GET` returns
  `{goal, savedSoFar}` (goal is `null` if unset), `PUT` sets/replaces it
  (422 on a non-positive amount), `DELETE` clears it (404 if none set).
- **Data export + account deletion** (`app/routers/account.py`) — Kenya's
  Data Protection Act 2019 rights the Terms & Conditions already promised
  but couldn't back up with real functionality. `GET /v1/data-export` returns
  everything tied to the account (profile, both periods' metrics, cash flow,
  savings goal, grants, pending requests) as a downloadable JSON file.
  `DELETE /v1/account` calls `store.reset_to_defaults()`, which just re-runs
  `Store.__init__()` rather than duplicating the default values a second
  time. **This also fixed a real gap, not just added a new feature**: the
  frontend's "Reset account" button previously only cleared browser
  localStorage — the backend's profile/score/grants silently survived a
  "reset" because nothing ever called back into this store. It now calls
  `DELETE /v1/account` first. Verified: after calling it, `/v1/profile`
  returns `{"name": null}` and `/v1/requests` is back to the single seeded
  grant, matching a truly fresh store.
- **In-app notifications** (`store.notifications`, `routers/notifications.py`)
  — chosen over push/email/SMS for this pass (no external service, no cost,
  no credentials). Two trigger points call `store.add_notification()`:
  every completed score computation (`statements.py`'s `_notify_score_change`,
  skipped when delta is exactly 0) and every simulated incoming consent
  request (`consent.py`). `GET /v1/notifications` returns newest-first;
  `POST /v1/notifications/{id}/read` and `.../read-all` mark them read.
  Verified live: simulating a request and uploading a statement produced
  both notification kinds in the right order, and the frontend's bell badge
  correctly showed the unread count.

**Reload gotcha bit again during this sprint**: adding a brand-new router
file and importing it into `main.py` sometimes doesn't trigger a working
WatchFiles reload — the process stays up but silently keeps serving the old
route table (new endpoints 404 despite the code being correct, confirmed by
importing the module directly in a one-off Python check). When this
happens, a plain in-process reload isn't enough; kill *all* `python.exe`
processes for this project (a `--reload` run is a reloader parent plus a
server child — killing only one PID can leave the other as an orphan still
holding the port, and after several restart attempts multiple orphans can
pile up on port 8000 at once) and start uvicorn fresh. Symptom to watch for:
`netstat -ano | findstr :8000` showing more than one PID in `LISTENING` state.

## Security

Honest framing up front: **no system is "unhackable"** — that's not a real
engineering target, and this section doesn't claim it. What follows is
defense-in-depth applied where it's genuinely load-bearing, plus a clear list
of what's still a gap and why it can't be closed without the real backend.

### Real, in place now

- **Real multi-tenancy and real authentication** (added in "Supabase
  migration, pass 1", below) — every table is a real Postgres table scoped by
  Row Level Security to `auth.uid()`, and every account is a real Supabase
  Auth user. This replaces what used to be the two biggest gaps in this
  section: a single process-wide in-memory store with no concept of "a
  user", and every request acting as the same mock account.
- **Security response headers** (`app/main.py` middleware): `X-Content-Type-
  Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Strict-Transport-Security`. Cheap, standard, no downside. HSTS is inert
  over the plain HTTP this runs on in dev and becomes load-bearing the moment
  this is served over TLS.
- **Rate limiting** (`app/rate_limit.py`, `slowapi`, per-IP, in-memory):
  `/v1/statements/upload` at 10/min, `/v1/statements/{id}/classify` at
  20/min. Verified live — the 10th+ request in a burst gets a real 429, not
  just a documented intention. This is a real control against scripted
  flooding of the one endpoint that does real CPU work (PDF parsing)
  unauthenticated. A production deployment should *also* rate-limit at the
  platform/gateway level (Supabase Auth rate-limits sign-in attempts itself)
  — this app-level limiter doesn't disappear once that exists, since it
  covers endpoints Supabase's own limiter doesn't know about.
- **Upload size cap**: `/v1/statements/upload` rejects anything over 10 MB
  with a 413 before it reaches the PDF parser — verified live. Stops someone
  burning memory/CPU with an oversized POST before pypdf ever gets a chance
  to reject it as unparseable.
- **Statement handling**: verified true, not just claimed — the PDF bytes
  are read into memory, parsed, and go out of scope when the request
  returns. Nothing is written to disk. This must carry forward into the
  Supabase migration unchanged: only derived metrics get a table; the PDF
  itself never gets a storage bucket.
- **No known-vulnerable dependencies** — `pip-audit` against
  `requirements.txt` came back clean as of this pass. Worth re-running
  whenever a dependency bumps.
- **PIN brute-force lockout** (frontend, `scorewise-consumer/.../lib/
  session.ts`): escalating lockout (15s / 60s / 5min) after 3/5/8 wrong PINs,
  same idea as iOS/Android's on-device passcode lockout. Verified live. This
  raises the cost of someone picking up an unlocked device and guessing, or
  of naive scripted retries against the client — it does **not** protect
  against an attacker who can read/write the browser's storage directly,
  since they could just clear the lockout keys or set the unlocked flag
  outright. That class of attack is only closed by real server-side PIN
  verification (see below).

### Known gaps — real, not cosmetic, and why they're not fixed yet

Two gaps that used to live here — no multi-tenancy, no real authentication —
are resolved as of "Supabase migration, pass 1" below and moved up into "Real,
in place now". What's left:

- **PIN/session is 100% client-side** (see `scorewise-consumer` CLAUDE.md's
  security note) — this backend never sees or verifies a PIN. Hashing the
  PIN in `localStorage` was considered and deliberately **not** done: a
  4-digit PIN's keyspace (10,000 values) is trivially brute-forced offline
  the instant an attacker can read the hash, so hashing it client-side would
  be security theater, not a real improvement. The lockout above is the
  honest, real mitigation available without a backend; genuine PIN security
  requires server-side verification with the server itself rate-limiting
  attempts (making offline brute force impossible since each guess costs a
  network round trip against a server that eventually locks the account).
- **WebAuthn has no server side.** `enrollBiometric`/`verifyBiometric` make
  real browser calls but there's no server generating challenges or
  verifying attestation/assertion signatures — see the frontend's own note.
  Still true after this pass: real auth exists now, but it's Supabase's
  email/password auth, not WebAuthn — closing this needs a server generating
  and verifying challenges (e.g. via `py_webauthn`), which is separate work.
- **No encryption at rest for PII beyond RLS.** `account_name` now lives in a
  real `profiles` row, RLS-scoped so only its owner can read it — a real
  improvement over "nothing persists yet" — but it isn't additionally
  column-encrypted (Postgres `pgcrypto` / Supabase Vault). RLS controls *who*
  can query the row; it doesn't protect the raw column value from something
  with direct database access (e.g. the service_role key, or a backup).
- **No audit log** for consent grants/revokes and lender data access — still
  just `grants_table` current-state rows, no history of who accessed what
  when. Matters for a fintech app regardless of scale, and directly supports
  the accountability principle in Kenya's Data Protection Act 2019 that
  `termsContent.ts` already promises but can't yet back up with real logs.
- **No hard account deletion.** "Reset account" (`DELETE /v1/account`) wipes
  data rows and ends the session but deliberately does **not** delete the
  Supabase auth user — see "Supabase migration, pass 1" for why this was a
  deliberate product choice, not an oversight. A real right-to-erasure flow
  that removes the identity entirely still needs building (service_role
  `auth.admin.deleteUser`).
- **CORS is dev-only correct.** Locked to `localhost:5173` with
  `allow_credentials=True`, which is right for now, but must never widen to
  a wildcard origin — incompatible with credentialed CORS anyway, and this
  API carries financial data plus real session cookies.

## Supabase migration, pass 1: real auth + core data

Replaces the in-memory `Store` singleton with real Supabase Auth + Postgres,
RLS-scoped. Scope was deliberately limited to auth + core data — server-side
WebAuthn and an audit log table are still open (see "Known gaps" above).

**Architecture**: the frontend never talks to Supabase directly. This
backend is the only thing that calls Supabase's Auth REST API
(`{SUPABASE_URL}/auth/v1/...`, using the anon key as the `apikey` header),
and it re-issues the resulting access/refresh tokens to the browser as
`httpOnly`, `SameSite=Lax` cookies (`app/auth.py`) — never as JSON the
frontend could read and accidentally end up putting in `localStorage`. Data
access goes straight to Postgres via the session pooler (`app/db.py`,
`psycopg` async), not through PostgREST — every request opens a transaction
that runs `SET LOCAL ROLE authenticated` plus
`set_config('request.jwt.claims', ...)` before any query, which is what
makes `auth.uid()` resolve correctly and RLS the actual enforcement
mechanism, not an application-level `WHERE user_id = ...` that's one missed
line from a leak. **This matters because `DATABASE_URL` connects as the
`postgres` superuser**, which bypasses RLS by default — skipping the `SET
LOCAL ROLE` step would silently see every account's data.

**Schema**: `supabase/schema.sql` — `profiles`, `period_metrics`, `cash_flow`,
`pending_consents`, `grants_table` (named to avoid ambiguity with the SQL
`GRANT` statement), `savings_goals`, `notifications`. One RLS policy per
table (`for all ... using ((select auth.uid()) = user_id) with check (...)`,
wrapping `auth.uid()` in a subquery per Supabase's own performance guidance),
plus explicit `grant select, insert, update, delete ... to authenticated`
since tables created outside the dashboard table editor don't get that
automatically. `expires_at`/`created_at` on grants/notifications/savings
goals are `bigint` epoch-milliseconds, not `timestamptz` — matches the
existing JSON contract the frontend already expects (`ExpiryBadge`'s
countdown math, notification timestamps), not a real datetime column.
`pending_reviews` (in-flight ambiguous-statement-classification sessions) is
now a real table too (moved out of in-memory during the 2026-09-13
restructure pass, see `app/reviews_repo.py`) — each session carries a
`user_id` so one account can't classify another's pending session by
guessing/enumerating a session id, which the old single-tenant mock backend
had no way to even express as a risk, and it now also survives a restart.

**Real gotchas hit and fixed, worth knowing before touching this again**:
- **The direct `db.<ref>.supabase.co:5432` host is IPv6-only.** On a network
  without an IPv6 route (confirmed here via `Test-NetConnection` returning
  `DestinationNetworkUnreachable`), it fails to connect with no useful error
  beyond DNS resolving to an IPv6 address. Fix: use the **session pooler**
  connection string instead (`aws-<n>-<region>.pooler.supabase.com:5432`,
  username `postgres.<project-ref>`) — IPv4-reachable. Must be session mode,
  not transaction mode: transaction-mode pgbouncer can silently drop the
  `SET LOCAL` state this design depends on.
- **This Supabase project uses the newer asymmetric JWT signing keys
  (ES256), not the legacy shared HS256 secret.** The dashboard's "JWT
  Secret" field under JWT Settings showed a UUID (a Key ID) rather than a
  long random string — decoding a real access token's header confirmed
  `"alg":"ES256"` with a `kid`. Verifying with HS256 + that UUID as the key
  failed *silently* (caught as a generic `PyJWTError`, surfaced only as
  "not authenticated", no server error) rather than loudly, which made this
  easy to misdiagnose as a cookie/CORS problem instead of a wrong-algorithm
  problem. Fixed by verifying against the project's public JWKS instead
  (`jwt.PyJWKClient` on `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`) — see
  `app/auth.py`'s `verify_access_token`. No shared secret needed at all for
  this scheme; `SUPABASE_JWT_SECRET` is unused despite the name suggesting
  otherwise. If a future project shows a real long-string HS256 secret
  instead of a UUID, this function would need to branch on that.
- **`ES256` verification needs the `cryptography` package**, which isn't a
  transitive dependency of `PyJWT` by default — `jwt.decode(...,
  algorithms=["ES256"])` raises `MissingCryptographyError` without it.
  Installed and pinned in `requirements.txt`.
- **Restarting after installing a new dependency mid-session needs a full
  process kill, not just `--reload`'s file-watch restart** — same category
  as this file's existing WatchFiles gotcha, but for a different reason.
  `PyJWT`'s algorithm-availability flags are set once at `import jwt.algorithms`
  time; a file-watch reload re-imports *this app's* modules but the
  interpreter process itself (and therefore already-cached `sys.modules`
  state for third-party packages) doesn't restart, so a package installed
  after the process started can still fail as "not available" even though
  it's genuinely on disk and importable in a fresh process. Symptom: a fresh
  `python -c "import ..."` in a new process works, but the already-running
  `--reload` server keeps failing the same way. Fix is the same as the
  existing gotcha: kill all `python.exe` for this project and start clean.
- **Windows' default `ProactorEventLoop` can't run psycopg's async pool** —
  `run.py` (new) sets `WindowsSelectorEventLoopPolicy` before importing
  uvicorn, since `uvicorn.run()` creates its event loop before it imports the
  app module, making a policy set inside `app/__init__.py` too late for that
  path (kept anyway, harmlessly, for any entry point that imports the app
  before creating its own loop). Use `python run.py`, not
  `uvicorn app.main:app` directly, for local dev from now on.

**Product decisions made explicit during this pass** (confirmed with the
user, not just assumed):
- **"Reset account" is a soft reset, not a hard delete.** `DELETE
  /v1/account` wipes this account's rows and ends the session, but does not
  call Supabase's admin API to delete the auth user itself — the same
  email/password logs back in afterward and starts fresh. A literal "delete
  everything including the identity" flow was considered and deliberately
  not built in this pass; it would need the service_role key's admin API,
  which nothing currently calls.
- **A forgotten device PIN no longer means a forced account reset.** Now
  that the PIN (device lock) and the Supabase account (identity) are
  genuinely separate concerns, `PinEntry`'s escape hatch just signs out
  (clears cookies + local PIN state, back to Login) rather than wiping data
  — forcing a full data wipe over 4 forgotten digits would have been a
  much bigger blast radius than the problem it was solving.

**Verified, not just implemented**: signup creates a real `auth.users` row
and seeds the expected default rows across every table; two independently
signed-up accounts each see only their own profile/grants/notifications;
querying Postgres directly *as* the `authenticated` role scoped to one
account's `auth.uid()`, with no `WHERE` clause at all, returns only that
account's rows — confirming RLS itself is the enforcement boundary, not
application code that happens to filter correctly today.

## Deployment (Render)

This has run locally only until now. `render.yaml` is a Blueprint — connect
this repo in Render's dashboard (New + → Blueprint) and it picks up the
build/start commands automatically. Env vars marked `sync: false` in
`render.yaml` (`SUPABASE_URL`, `SUPABASE_ANON_KEY`,
`SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`, `ALLOWED_ORIGINS`) must be set
by hand in the dashboard after the service exists — copy the values from the
local `.env`, and set `ALLOWED_ORIGINS=https://scorewisee.netlify.app` (the
deployed frontend). `COOKIE_SECURE=true` and
`COOKIE_SAMESITE=none` are already set in the blueprint — required once the
frontend and backend are on genuinely different domains (see `app/auth.py`'s
comment on why `Lax` only worked for local dev's same-site-different-port
setup). Uses a plain `uvicorn app.main:app` start command, not `run.py` —
Render's Linux runtime doesn't hit the Windows event-loop issue `run.py`
exists to work around.

## Not done yet

- Real database (Supabase) and real auth / multi-user support — **done**, see
  "Supabase migration, pass 1" below. What's listed there as still deferred
  (server-side WebAuthn, an audit log table, hard account deletion) stays
  genuinely not done.
- **Email OTP signup** — still not built. Supabase Auth is configured now, so
  this is no longer blocked on credentials the way it was; it just hasn't
  been built as part of this pass, which focused on password auth.
- Lender app integration.
- Testing against a *second* real statement to see if the record-boundary
  parsing and the (amount, balance) reading generalize, or were specific to
  the one export tested. Testing the digital-lender keywords/paybill numbers
  against a statement that actually has that lender's activity.
- Fuzzy name matching (see "Account name verification" above).
- Ambiguous-group classification against a statement where a real loan is
  routed through a payment aggregator rather than a direct bank paybill.
- ~~Session expiry for `pending_reviews`~~ — done, see "Supabase migration,
  pass 1" above (moved to Postgres, 24h TTL).
- **Zero automated test suite** (no pytest, no CI) — flagged during the
  2026-09-13 restructure pass as the most consequential gap in this file;
  see `../SCOREWISE_BACKLOG.md`.
