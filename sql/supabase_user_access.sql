create table if not exists public.user_access (
  user_id uuid primary key references auth.users (id) on delete cascade,
  trial_started_at timestamptz,
  trial_ends_at timestamptz,
  subscription_active boolean not null default false,
  subscription_updated_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_user_access_trial_ends_at
  on public.user_access (trial_ends_at);

create index if not exists idx_user_access_subscription_active
  on public.user_access (subscription_active)
  where subscription_active = true;

alter table public.user_access enable row level security;

drop policy if exists "users_read_own_access" on public.user_access;
create policy "users_read_own_access"
on public.user_access
for select
to authenticated
using (auth.uid() = user_id);
