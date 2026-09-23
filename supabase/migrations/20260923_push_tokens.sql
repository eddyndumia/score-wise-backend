-- Device push tokens (Firebase Cloud Messaging) for the mobile app, so a
-- lender's request reaches the borrower's phone (docs/REQUIREMENTS.md B9).
-- Additive only. Safe to re-run.
--
-- A token identifies an app install, not a person. If someone signs out and
-- a different account signs in on the same phone, the token has to move to
-- the new owner, and RLS won't let one user touch another's row. So
-- registering goes through register_push_token(), which removes the token
-- from whoever had it and gives it to the caller. Nothing else is exposed.

create table if not exists push_tokens (
  token text primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  platform text not null check (platform in ('android', 'ios', 'web')),
  updated_at timestamptz not null default now()
);

create index if not exists push_tokens_user_idx on push_tokens (user_id);

alter table push_tokens enable row level security;

drop policy if exists "owner_all" on push_tokens;
create policy "owner_all" on push_tokens for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

grant select, insert, update, delete on push_tokens to authenticated;

create or replace function public.register_push_token(p_token text, p_platform text)
returns void
language plpgsql security definer set search_path = public
as $$
begin
  if auth.uid() is null then
    raise exception 'not signed in' using errcode = '42501';
  end if;
  insert into push_tokens (token, user_id, platform, updated_at)
  values (p_token, auth.uid(), p_platform, now())
  on conflict (token) do update
    set user_id = excluded.user_id, platform = excluded.platform, updated_at = now();
end;
$$;

revoke all on function public.register_push_token(text, text) from public;
grant execute on function public.register_push_token(text, text) to authenticated;
