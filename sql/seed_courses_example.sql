-- Seed published courses for Sentient pricing screen
--
-- Run AFTER supabase_course_catalog_only.sql (or after courses table exists).
-- Safe to re-run (ON CONFLICT updates title/description/is_published).
--
-- Stripe Price IDs: set STRIPE_PRICE_* on BOTH frontend and backend env,
-- OR insert course_products rows below (preferred for production).

begin;

insert into public.courses (course_slug, title, description, is_published)
values
  (
    'week-zero-reset',
    'Week Zero Reset',
    'Foundational reset course for building calm daily habits.',
    true
  ),
  (
    'deep-calm-protocol',
    'Deep Calm Protocol',
    'Structured practice for deeper relaxation and nervous system regulation.',
    true
  ),
  (
    'focus-discipline',
    'Focus & Discipline',
    'Training for attention, consistency, and mindful productivity.',
    true
  ),
  (
    'starter-bundle',
    'Starter Bundle',
    'Bundle of core Sentient courses with lifetime access.',
    true
  ),
  (
    'mindful-foundations',
    'Mindful Foundations',
    'Daily guided practice with schedule-based chat lessons.',
    true
  )
on conflict (course_slug) do update
set
  title = excluded.title,
  description = excluded.description,
  is_published = excluded.is_published,
  updated_at = timezone('utc', now());

-- Optional: link Stripe prices (replace prod_xxx and price_xxx with real Stripe IDs)
-- Uncomment and edit after creating products in Stripe Dashboard.
--
-- insert into public.course_products (
--   course_slug,
--   provider_product_id,
--   provider_price_id,
--   unit_amount_cents,
--   currency,
--   is_active
-- )
-- values
--   ('week-zero-reset', 'prod_REPLACE', 'price_REPLACE', 2400, 'usd', true),
--   ('deep-calm-protocol', 'prod_REPLACE', 'price_REPLACE', 3900, 'usd', true),
--   ('focus-discipline', 'prod_REPLACE', 'price_REPLACE', 2900, 'usd', true),
--   ('starter-bundle', 'prod_REPLACE', 'price_REPLACE', 6900, 'usd', true)
-- on conflict (course_slug) do update
-- set
--   provider_product_id = excluded.provider_product_id,
--   provider_price_id = excluded.provider_price_id,
--   unit_amount_cents = excluded.unit_amount_cents,
--   is_active = true,
--   updated_at = timezone('utc', now());

commit;

-- Verify:
-- select course_slug, title, is_published from public.courses order by title;
