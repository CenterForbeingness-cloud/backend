-- Phase 2 memory tables (facts, goals, events)
-- Requires: auth.users
-- Guide: MVP_NORTH_STAR.md

begin;

create table if not exists public.user_facts (
  id bigserial primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  fact text not null,
  confidence real not null default 0.8 check (confidence >= 0 and confidence <= 1),
  source_message_id bigint,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_user_facts_user_created
  on public.user_facts (user_id, created_at desc);

create table if not exists public.user_goals (
  id bigserial primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null,
  status text not null default 'active',
  progress integer not null default 0 check (progress >= 0 and progress <= 100),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_user_goals_user_status
  on public.user_goals (user_id, status);

create table if not exists public.memory_events (
  id bigserial primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  event_type text not null,
  event_summary text not null,
  importance smallint not null default 3 check (importance >= 1 and importance <= 5),
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_memory_events_user_created
  on public.memory_events (user_id, created_at desc);

alter table public.user_facts enable row level security;
alter table public.user_goals enable row level security;
alter table public.memory_events enable row level security;

-- Backend reads/writes via SUPABASE_DB_URL; add client policies when exposing to Flutter.

commit;
