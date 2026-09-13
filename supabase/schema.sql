-- ScoreWise consumer app — core data schema, pass 1 (auth + core data).
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

-- Not using PostgREST/the Data API in this design (the backend talks to
-- Postgres directly via the session pooler), so Data API exposure settings
-- don't apply — but the `authenticated` role still needs plain SQL grants
-- since these tables weren't created through the dashboard table editor.
grant usage on schema public to authenticated;
grant select, insert, update, delete on all tables in schema public to authenticated;
