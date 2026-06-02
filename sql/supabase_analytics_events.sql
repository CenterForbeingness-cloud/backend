-- MVP Launch: product + business analytics events
-- Run in Supabase SQL editor (service role).

create table if not exists public.analytics_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users (id) on delete set null,
  event_name text not null,
  properties jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_analytics_events_name_created
  on public.analytics_events (event_name, created_at desc);

create index if not exists idx_analytics_events_user_created
  on public.analytics_events (user_id, created_at desc);

alter table public.analytics_events enable row level security;

-- Authenticated users may insert their own events (client batch flush).
drop policy if exists analytics_events_insert_own on public.analytics_events;
create policy analytics_events_insert_own
  on public.analytics_events
  for insert
  to authenticated
  with check (auth.uid() = user_id);

-- Reads: service role / dashboard only (no anon select).
drop policy if exists analytics_events_select_service on public.analytics_events;
create policy analytics_events_select_service
  on public.analytics_events
  for select
  to service_role
  using (true);
