-- Sentient course ownership and Stripe billing schema (first slice)
-- Scope: schema, constraints, indexes, and RLS only.
-- Non-goal: checkout/webhook business logic.

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

-- Course catalog tables --

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

-- Stripe product and ownership tables --

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

create table if not exists public.course_purchases (
  id bigserial primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  course_slug text not null references public.courses(course_slug) on delete cascade,
  provider text not null default 'stripe' check (provider in ('stripe')),
  provider_checkout_session_id text,
  provider_payment_intent_id text,
  provider_charge_id text,
  provider_customer_id text,
  amount_cents integer not null check (amount_cents >= 0),
  currency text not null default 'usd',
  status text not null default 'pending' check (
    status in ('pending', 'paid', 'refunded', 'revoked', 'failed')
  ),
  purchased_at timestamptz,
  refunded_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (user_id, course_slug),
  unique (provider, provider_checkout_session_id),
  unique (provider, provider_payment_intent_id),
  unique (provider, provider_charge_id)
);

drop trigger if exists trg_course_purchases_updated_at on public.course_purchases;
create trigger trg_course_purchases_updated_at
before update on public.course_purchases
for each row execute function public.set_updated_at();

create index if not exists idx_course_purchases_user_status
  on public.course_purchases(user_id, status, created_at desc);

create index if not exists idx_course_purchases_course_status
  on public.course_purchases(course_slug, status, created_at desc);

create table if not exists public.user_entitlements (
  id bigserial primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  course_slug text not null references public.courses(course_slug) on delete cascade,
  source text not null check (source in ('purchase', 'grant', 'trial')),
  source_ref text,
  is_active boolean not null default true,
  starts_at timestamptz not null default timezone('utc', now()),
  expires_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (user_id, course_slug)
);

drop trigger if exists trg_user_entitlements_updated_at on public.user_entitlements;
create trigger trg_user_entitlements_updated_at
before update on public.user_entitlements
for each row execute function public.set_updated_at();

create index if not exists idx_user_entitlements_user_active
  on public.user_entitlements(user_id, is_active, expires_at);

create table if not exists public.purchase_events (
  id bigserial primary key,
  provider text not null default 'stripe' check (provider in ('stripe')),
  provider_event_id text not null,
  event_type text not null,
  payload jsonb not null,
  status text not null default 'received' check (
    status in ('received', 'processed', 'ignored', 'failed')
  ),
  processed_at timestamptz,
  error_text text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (provider, provider_event_id)
);

drop trigger if exists trg_purchase_events_updated_at on public.purchase_events;
create trigger trg_purchase_events_updated_at
before update on public.purchase_events
for each row execute function public.set_updated_at();

create index if not exists idx_purchase_events_status_created
  on public.purchase_events(status, created_at desc);

-- RLS policies --

alter table public.courses enable row level security;
alter table public.course_weeks enable row level security;
alter table public.course_lessons enable row level security;
alter table public.course_products enable row level security;
alter table public.course_purchases enable row level security;
alter table public.user_entitlements enable row level security;
alter table public.purchase_events enable row level security;

-- Public course catalog visibility is limited to published and active items.
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

-- Users can read only their own ownership and entitlement records.
drop policy if exists "users_read_own_course_purchases" on public.course_purchases;
create policy "users_read_own_course_purchases"
on public.course_purchases
for select
to authenticated
using (auth.uid() = user_id);

drop policy if exists "users_read_own_entitlements" on public.user_entitlements;
create policy "users_read_own_entitlements"
on public.user_entitlements
for select
to authenticated
using (auth.uid() = user_id);

-- No client-side access to webhook event payloads.
-- Intentionally no policies on purchase_events.

commit;
