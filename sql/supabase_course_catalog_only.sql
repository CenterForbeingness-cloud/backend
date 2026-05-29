-- Sentient course catalog only (safe when entitlements/billing tables already exist)
--
-- Use this when you already ran supabase_entitlements_rls.sql and got:
--   ERROR: column "status" does not exist
-- when running supabase_courses_billing_rls.sql.
--
-- Reason: entitlements_rls.sql created course_purchases and purchase_events with a
-- different schema (purchase_source, processing_status). The billing migration skips
-- CREATE TABLE but still tries to index a "status" column that does not exist.
--
-- This script creates ONLY the public catalog tables:
--   courses, course_weeks, course_lessons, course_products
-- It does NOT recreate course_purchases, user_entitlements, or purchase_events.
--
-- Run in Supabase SQL Editor. Safe to re-run.

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

-- Catalog tables --

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

create table if not exists public.course_weeks (
  id bigserial primary key,
  course_slug text not null references public.courses(course_slug) on delete cascade,
  week_number integer not null check (week_number > 0),
  title text not null,
  description text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (course_slug, week_number)
);

drop trigger if exists trg_course_weeks_updated_at on public.course_weeks;
create trigger trg_course_weeks_updated_at
before update on public.course_weeks
for each row execute function public.set_updated_at();

create table if not exists public.course_lessons (
  id bigserial primary key,
  week_id bigint not null references public.course_weeks(id) on delete cascade,
  lesson_number integer not null check (lesson_number > 0),
  title text not null,
  content_ref text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (week_id, lesson_number)
);

drop trigger if exists trg_course_lessons_updated_at on public.course_lessons;
create trigger trg_course_lessons_updated_at
before update on public.course_lessons
for each row execute function public.set_updated_at();

create index if not exists idx_course_weeks_course_week
  on public.course_weeks(course_slug, week_number);

create index if not exists idx_course_lessons_week_lesson
  on public.course_lessons(week_id, lesson_number);

create table if not exists public.course_products (
  id bigserial primary key,
  course_slug text not null unique references public.courses(course_slug) on delete cascade,
  provider text not null default 'stripe' check (provider in ('stripe')),
  provider_product_id text not null,
  provider_price_id text not null,
  currency text not null default 'usd',
  unit_amount_cents integer not null check (unit_amount_cents >= 0),
  is_active boolean not null default true,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (provider, provider_product_id),
  unique (provider, provider_price_id)
);

drop trigger if exists trg_course_products_updated_at on public.course_products;
create trigger trg_course_products_updated_at
before update on public.course_products
for each row execute function public.set_updated_at();

-- RLS for catalog tables only --

alter table public.courses enable row level security;
alter table public.course_weeks enable row level security;
alter table public.course_lessons enable row level security;
alter table public.course_products enable row level security;

drop policy if exists "public_can_read_published_courses" on public.courses;
create policy "public_can_read_published_courses"
on public.courses
for select
to anon, authenticated
using (is_published = true);

drop policy if exists "public_can_read_published_weeks" on public.course_weeks;
create policy "public_can_read_published_weeks"
on public.course_weeks
for select
to anon, authenticated
using (
  exists (
    select 1 from public.courses c
    where c.course_slug = course_weeks.course_slug
      and c.is_published = true
  )
);

drop policy if exists "public_can_read_published_lessons" on public.course_lessons;
create policy "public_can_read_published_lessons"
on public.course_lessons
for select
to anon, authenticated
using (
  exists (
    select 1
    from public.course_weeks w
    join public.courses c on c.course_slug = w.course_slug
    where w.id = course_lessons.week_id
      and c.is_published = true
  )
);

drop policy if exists "public_can_read_active_course_products" on public.course_products;
create policy "public_can_read_active_course_products"
on public.course_products
for select
to anon, authenticated
using (is_active = true);

commit;
