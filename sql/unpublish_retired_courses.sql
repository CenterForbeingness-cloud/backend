-- Drop and rename catalog plan — slice 3 (database)
-- Date: 30 September 2026
--
-- Purpose:
-- Unpublish retired one time products so they leave the main published catalog.
-- Do not delete rows. Entitlement history stays.
--
-- Run the whole file in Supabase SQL Editor.
-- Safe to re-run.

-- 1) Show what exists before the change.
select
  course_slug,
  title,
  is_published as is_published_before
from public.courses
order by course_slug;

-- 2) Unpublish retired products and return the rows that changed.
update public.courses
set
  is_published = false,
  updated_at = timezone('utc', now())
where course_slug in (
  'week-zero-reset',
  'deep-calm-protocol',
  'focus-discipline',
  'starter-bundle'
)
returning
  course_slug,
  title,
  is_published as is_published_after_update;

-- 3) Final catalog state.
select
  course_slug,
  title,
  is_published
from public.courses
order by course_slug;
