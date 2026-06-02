-- MVP Launch: daily voice seconds cap (separate from message count quota)

create table if not exists public.user_voice_usage (
  user_id uuid primary key references auth.users (id) on delete cascade,
  voice_seconds_today numeric not null default 0,
  period_start timestamptz not null,
  last_updated_at timestamptz not null default now()
);

create index if not exists idx_user_voice_usage_period
  on public.user_voice_usage (user_id, period_start);

alter table public.user_voice_usage enable row level security;

drop policy if exists user_voice_usage_select_own on public.user_voice_usage;
create policy user_voice_usage_select_own
  on public.user_voice_usage
  for select
  to authenticated
  using (auth.uid() = user_id);
