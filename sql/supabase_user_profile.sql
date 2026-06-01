-- Thin memory (Phase 1): one row per user.
-- Run in Supabase SQL Editor after auth is enabled.

create table if not exists public.user_profile (
  user_id uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  primary_goal text,
  secondary_goal text,
  current_focus text,
  energy_level text,
  motivation_type text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_user_profile_updated_at
  on public.user_profile (updated_at desc);

alter table public.user_profile enable row level security;

drop policy if exists "users_manage_own_profile" on public.user_profile;
create policy "users_manage_own_profile"
on public.user_profile
for all
to authenticated
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

-- Backend service role (Railway) bypasses RLS when using direct Postgres URL.
