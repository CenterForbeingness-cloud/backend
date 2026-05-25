-- Per-user current day for daily schedule courses
-- Requires: public.courses (from supabase_course_daily_schedule.sql or billing migration)
-- Guide: schedules/README.md

begin;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

create table if not exists public.user_course_progress (
  id bigserial primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  course_slug text not null references public.courses(course_slug) on delete cascade,
  current_day_number integer not null default 1 check (current_day_number > 0),
  started_at timestamptz not null default timezone('utc', now()),
  last_activity_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (user_id, course_slug),
  constraint valid_progress_course_slug check (course_slug ~ '^[a-z0-9\-]+$')
);

drop trigger if exists trg_user_course_progress_updated_at on public.user_course_progress;
create trigger trg_user_course_progress_updated_at
before update on public.user_course_progress
for each row execute function public.set_updated_at();

create index if not exists idx_user_course_progress_user_course
  on public.user_course_progress(user_id, course_slug);

alter table public.user_course_progress enable row level security;

-- Backend reads/writes via SUPABASE_DB_URL; add client policies when exposing progress API to Flutter.

commit;
