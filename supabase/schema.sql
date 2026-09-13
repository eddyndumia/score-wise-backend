-- PesaScore consumer app — core data schema, pass 1 (auth + core data).
-- Applied directly against DATABASE_URL by the backend session that wrote
-- this file. Safe to re-run: every statement is idempotent.
--
-- RLS gotcha this schema depends on: DATABASE_URL connects as the `postgres`
-- superuser, which bypasses RLS by default. The backend must run every
-- per-user query inside a transaction that does
--   SET LOCAL ROLE authenticated;
--   SET LOCAL request.jwt.claims = '{"sub":"<uuid>","role":"authenticated"}';
-- (see app/db.py) — RLS is what enforces per-user isolation, not an
-- application-level WHERE clause.
--
-- Policies wrap auth.uid() in a scalar subquery ((select auth.uid())) and
-- target `to authenticated` explicitly, per Supabase's own RLS guidance —
-- avoids a per-row function call and avoids relying on the deprecated
-- auth.role() pattern.

create table if not exists profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  account_name text,
  profile_name text,
  created_at timestamptz not null default now()
);
-- Denormalized copy of the borrower's auth.users email — added so a lender's
-- RLS-scoped query (routers/lender.py) can build a masked identifier without
-- touching auth.users, which the `authenticated` role has no grant on at all
-- (only the SECURITY DEFINER function below can read it). Populated at
-- signup (app/seed.py); pre-existing rows created before this pass are left
-- null (masked as "unknown") rather than backfilled.
alter table profiles add column if not exists email text;

create table if not exists period_metrics (
  user_id uuid not null references auth.users(id) on delete cascade,
  period text not null check (period in ('current', 'previous')),
  repayments jsonb not null,
  fuliza jsonb not null,
  savings jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (user_id, period)
);

create table if not exists cash_flow (
  user_id uuid primary key references auth.users(id) on delete cascade,
  series jsonb not null,
  updated_at timestamptz not null default now()
);

create table if not exists pending_consents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  lender_name text not null,
  grant_duration_days int not null,
  will_share jsonb not null,
  wont_share jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists pending_consents_user_id_idx on pending_consents (user_id);

-- expires_at is epoch-milliseconds (bigint), not timestamptz: the frontend
-- reads it directly as a JS Date-compatible number (ExpiryBadge's countdown
-- math) and this keeps the JSON contract identical to the old in-memory store.
create table if not exists grants_table (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  lender_name text not null,
  expires_at bigint not null,
  created_at timestamptz not null default now()
);
create index if not exists grants_table_user_id_idx on grants_table (user_id);

-- Real lender identity (2026-09-13 pass — see PESASCORE_BACKLOG.md's old
-- "Lender app real backend integration" entry). Mirrors `profiles`'s shape:
-- one row per Supabase Auth user, this time an org account instead of a
-- borrower. `lender_name` on pending_consents/grants_table stays a display
-- snapshot for the consumer app's existing lenderName-only DTOs; lender_id
-- is the real, RLS-checkable link. Both are nullable additions so every
-- existing seeded/simulated row (lender_id null) is untouched.
create table if not exists lenders (
  id uuid primary key references auth.users(id) on delete cascade,
  org_name text not null,
  created_at timestamptz not null default now()
);

alter table pending_consents add column if not exists lender_id uuid references lenders(id) on delete set null;
alter table grants_table add column if not exists lender_id uuid references lenders(id) on delete set null;
-- Category-key array (see app/consent_categories.py) — lives on grants_table
-- too (not just pending_consents) so real per-grant enforcement survives
-- past approval, into the long-lived grant a lender endpoint actually reads.
alter table grants_table add column if not exists will_share jsonb not null default '[]'::jsonb;

-- Lender request-status tracking (2026-09-13). pending_consents rows used
-- to be hard-deleted the moment a borrower responded (approve -> moved into
-- grants_table, deny -> gone with no trace) — fine for the borrower's own
-- "waiting for your response" list, but it meant a lender could never see
-- what happened to a request it sent. Rows are now kept and marked
-- resolved instead of deleted, which doubles as a genuine (if minimal)
-- audit trail of what was asked and how it was answered.
alter table pending_consents add column if not exists status text not null default 'pending'
  check (status in ('pending', 'approved', 'denied'));
-- Denormalized like profiles.email, for the same reason: a lender's RLS
-- (lender_read_own_pending, below) covers this row before any grant exists,
-- but there's no policy letting a lender join to profiles/auth.users pre-
-- approval. Populated by create_lender_consent_request; null for demo/
-- simulated rows (lender_id null), which never appear in a lender's own view.
alter table pending_consents add column if not exists borrower_email text;

create index if not exists pending_consents_lender_id_idx on pending_consents (lender_id);
create index if not exists grants_table_lender_id_idx on grants_table (lender_id);

-- created_at is epoch-milliseconds (bigint) for the same reason — it's
-- serialized straight to the frontend as "createdAt".
create table if not exists savings_goals (
  user_id uuid primary key references auth.users(id) on delete cascade,
  target_amount numeric not null check (target_amount > 0),
  created_at bigint not null
);

create table if not exists notifications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  kind text not null,
  message text not null,
  read boolean not null default false,
  created_at bigint not null
);
create index if not exists notifications_user_id_idx on notifications (user_id);

-- Ambiguous-statement classification sessions awaiting the user's yes/no
-- answers (see app/reviews_repo.py). Used to live as a bare in-process dict
-- (app/store.py) — moved here because that dict didn't survive a Render
-- restart/idle-spindown, silently losing a user's in-progress review.
-- expires_at is timestamptz (unlike the epoch-ms columns above) since this
-- is a server-internal TTL never read by the frontend, not a JSON contract
-- field. Lazily expired on read (app/reviews_repo.py) rather than swept by a
-- cron — cheap enough at this volume.
create table if not exists pending_reviews (
  session_id text primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  rows jsonb not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null
);
create index if not exists pending_reviews_user_id_idx on pending_reviews (user_id);

alter table profiles enable row level security;
alter table period_metrics enable row level security;
alter table cash_flow enable row level security;
alter table pending_consents enable row level security;
alter table grants_table enable row level security;
alter table savings_goals enable row level security;
alter table notifications enable row level security;
alter table pending_reviews enable row level security;
alter table lenders enable row level security;

drop policy if exists "owner_all" on profiles;
create policy "owner_all" on profiles for all to authenticated
  using ((select auth.uid()) = id) with check ((select auth.uid()) = id);

drop policy if exists "owner_all" on period_metrics;
create policy "owner_all" on period_metrics for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "owner_all" on cash_flow;
create policy "owner_all" on cash_flow for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "owner_all" on pending_consents;
create policy "owner_all" on pending_consents for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "owner_all" on grants_table;
create policy "owner_all" on grants_table for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "owner_all" on savings_goals;
create policy "owner_all" on savings_goals for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "owner_all" on notifications;
create policy "owner_all" on notifications for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "owner_all" on pending_reviews;
create policy "owner_all" on pending_reviews for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "owner_all" on lenders;
create policy "owner_all" on lenders for all to authenticated
  using ((select auth.uid()) = id) with check ((select auth.uid()) = id);

-- Additive lender-facing SELECT policies below. Postgres combines multiple
-- permissive policies on the same command with OR, so these only ever ADD
-- visibility for a lender's own uid — they never weaken the owner_all
-- policies above, which still fully govern the borrower's own access.

-- A lender reading its own outgoing pending requests / grants (metadata
-- only — no borrower profile/score data is exposed by these two).
drop policy if exists "lender_read_own_pending" on pending_consents;
create policy "lender_read_own_pending" on pending_consents for select to authenticated
  using ((select auth.uid()) = lender_id);

drop policy if exists "lender_read_own_grants" on grants_table;
create policy "lender_read_own_grants" on grants_table for select to authenticated
  using ((select auth.uid()) = lender_id);

-- The policies that actually make a lender's dashboard real: a lender may
-- read a borrower's profile/period_metrics only while an UNEXPIRED grant
-- naming that exact lender exists. A revoke (existing DELETE endpoint) or
-- simple expiry (nothing needs to actively run — the expires_at > now
-- check just stops matching) both immediately cut off visibility.
drop policy if exists "lender_read_granted_profiles" on profiles;
create policy "lender_read_granted_profiles" on profiles for select to authenticated
  using (exists (
    select 1 from grants_table g
    where g.user_id = profiles.id
      and g.lender_id = (select auth.uid())
      and g.expires_at > (extract(epoch from now()) * 1000)::bigint
  ));

drop policy if exists "lender_read_granted_period_metrics" on period_metrics;
create policy "lender_read_granted_period_metrics" on period_metrics for select to authenticated
  using (exists (
    select 1 from grants_table g
    where g.user_id = period_metrics.user_id
      and g.lender_id = (select auth.uid())
      and g.expires_at > (extract(epoch from now()) * 1000)::bigint
  ));

-- Not using PostgREST/the Data API in this design (the backend talks to
-- Postgres directly via the session pooler), so Data API exposure settings
-- don't apply — but the `authenticated` role still needs plain SQL grants
-- since these tables weren't created through the dashboard table editor.
grant usage on schema public to authenticated;
grant select, insert, update, delete on all tables in schema public to authenticated;

-- A lender's consent request names a borrower `user_id` that is NOT the
-- inserting principal — no `with check` column-equality clause can express
-- "an authenticated lender may insert a row on behalf of any borrower it
-- correctly identifies." This function does its own authorization check in
-- the body instead of relying on RLS's with check, and is deliberately
-- narrow: the sharing package is fixed and platform-defined (no per-field
-- consent picker exists anywhere in this app — approval is a single
-- Allow/Deny), so a lender can never author its own will_share/wont_share
-- text or spoof a different display name than its own real org_name.
create or replace function public.create_lender_consent_request(
  p_borrower_email text,
  p_grant_duration_days int
) returns pending_consents
language plpgsql security definer set search_path = public
as $$
declare
  v_lender_id uuid := auth.uid();
  v_org_name text;
  v_borrower_id uuid;
  v_borrower_email text;
  v_row pending_consents;
begin
  select org_name into v_org_name from lenders where id = v_lender_id;
  if v_org_name is null then
    raise exception 'not a registered lender' using errcode = '42501';
  end if;

  select p.id, u.email into v_borrower_id, v_borrower_email
  from auth.users u join profiles p on p.id = u.id
  where lower(u.email) = lower(p_borrower_email);

  if v_borrower_id is null then
    raise exception 'no PesaScore borrower account found for that email' using errcode = 'P0002';
  end if;

  insert into pending_consents (user_id, lender_id, lender_name, borrower_email, grant_duration_days, will_share, wont_share)
  values (
    v_borrower_id, v_lender_id, v_org_name, v_borrower_email, p_grant_duration_days,
    '["repayment_history","savings_activity","fuliza_reliance","account_age"]'::jsonb,
    '["Full transaction amounts","Contact list","Balances on other accounts"]'::jsonb
  )
  returning * into v_row;

  -- Also done here (not by the calling Python code under the lender's own
  -- RLS-scoped connection) for the same reason as the insert above: this
  -- notification's user_id is the BORROWER, and the lender's restricted role
  -- can't satisfy notifications' owner_all `with check` for a row it doesn't
  -- own. Same security-definer authorization boundary covers both inserts.
  insert into notifications (user_id, kind, message, created_at)
  values (v_borrower_id, 'consent_request', v_org_name || ' wants access to your credit profile.', (extract(epoch from now()) * 1000)::bigint);

  return v_row;
end;
$$;

revoke all on function public.create_lender_consent_request(text, int) from public;
grant execute on function public.create_lender_consent_request(text, int) to authenticated;
