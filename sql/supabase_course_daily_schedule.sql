-- Daily course schedule (one row per day, keyed by course_slug + day_number)
--
-- Full setup guide: schedules/README.md
-- Backend reference: backend/README.md § Daily Course Schedule
--
-- After this migration:
--   1. INSERT INTO public.courses (...) for your course_slug
--   2. python scripts/import_daily_schedule.py --course-slug <slug> --file <path>
--   3. Run supabase_user_course_progress.sql for per-user current day
--
-- Safe to run standalone: creates public.courses if missing (same shape as
-- supabase_courses_billing_rls.sql). If you already ran the full billing migration,
-- this only adds course_daily_schedule.

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

-- Prerequisite for FK: minimal course catalog row per slug
create table if not exists public.courses (
  course_slug text primary key,
  title text not null,
  description text,
  is_published boolean not null default false,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

drop trigger if exists trg_courses_updated_at on public.courses;
create trigger trg_courses_updated_at
before update on public.courses
for each row execute function public.set_updated_at();

create table if not exists public.course_daily_schedule (
  id bigserial primary key,
  course_slug text not null references public.courses(course_slug) on delete cascade,
  day_number integer not null check (day_number > 0),
  day_title text,
  content text not null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (course_slug, day_number),
  constraint valid_schedule_course_slug check (course_slug ~ '^[a-z0-9\-]+$')
);

drop trigger if exists trg_course_daily_schedule_updated_at on public.course_daily_schedule;
create trigger trg_course_daily_schedule_updated_at
before update on public.course_daily_schedule
for each row execute function public.set_updated_at();

create index if not exists idx_course_daily_schedule_course_day
  on public.course_daily_schedule(course_slug, day_number);

alter table public.course_daily_schedule enable row level security;

-- No client policies yet: the FastAPI backend reads schedule rows via SUPABASE_DB_URL
-- (service/direct Postgres) and injects them into chat. Add a select policy when exposing
-- schedule via an authenticated API.

commit;
