-- Consent and access audit log (docs/REQUIREMENTS.md P4; Kenya Data
-- Protection Act 2019 accountability principle). Additive only: a new table,
-- its policies and grants. Nothing existing changes. Safe to re-run.
--
-- Append-only. The app's role gets select + insert and nothing else, and
-- RLS has no update/delete policy, so a row can't be edited or removed by
-- the app, only by the database owner. Rows go when the borrower's auth user
-- is deleted (right to erasure), not when they soft-reset their data.
--
-- Who can write what:
--   borrower: request_approved, request_denied, access_revoked (about themselves)
--   lender:   request_sent (only for a request it really sent)
--             score_viewed (only while it holds an unexpired grant for that borrower)
-- Who can read: a borrower sees everything about them; a lender sees only
-- its own rows.

create table if not exists access_log (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  borrower_id uuid not null references auth.users(id) on delete cascade,
  lender_id uuid references lenders(id) on delete set null,
  -- Denormalised so the borrower still sees who it was if the lender account goes.
  lender_name text not null,
  event text not null check (event in (
    'request_sent', 'request_approved', 'request_denied', 'access_revoked', 'score_viewed'
  )),
  detail jsonb not null default '{}'::jsonb
);

create index if not exists access_log_borrower_idx on access_log (borrower_id, created_at desc);
create index if not exists access_log_lender_idx on access_log (lender_id, created_at desc);

alter table access_log enable row level security;

drop policy if exists "borrower_read_own" on access_log;
create policy "borrower_read_own" on access_log for select to authenticated
  using ((select auth.uid()) = borrower_id);

drop policy if exists "lender_read_own" on access_log;
create policy "lender_read_own" on access_log for select to authenticated
  using ((select auth.uid()) = lender_id);

drop policy if exists "borrower_insert_own_decisions" on access_log;
create policy "borrower_insert_own_decisions" on access_log for insert to authenticated
  with check (
    (select auth.uid()) = borrower_id
    and event in ('request_approved', 'request_denied', 'access_revoked')
  );

drop policy if exists "lender_insert_own_actions" on access_log;
create policy "lender_insert_own_actions" on access_log for insert to authenticated
  with check (
    (select auth.uid()) = lender_id
    and (
      (event = 'request_sent' and exists (
        select 1 from pending_consents c
        where c.lender_id = (select auth.uid()) and c.user_id = access_log.borrower_id
      ))
      or (event = 'score_viewed' and exists (
        select 1 from grants_table g
        where g.lender_id = (select auth.uid()) and g.user_id = access_log.borrower_id
          and g.expires_at > (extract(epoch from now()) * 1000)::bigint
      ))
    )
  );

grant select, insert on access_log to authenticated;
revoke update, delete, truncate, references, trigger on access_log from authenticated;
