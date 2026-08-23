-- Additive: Ben onboarding JSON on existing user_profile rows.
-- Run in Supabase SQL Editor after sql/supabase_user_profile.sql.
-- New installs that create the table from supabase_user_profile.sql already
-- have this column; ADD COLUMN IF NOT EXISTS is safe either way.
--
-- JSON shape (stable keys; do not store display labels only):
--   path_stage            text
--   primary_reason        text
--   prior_experience      text[]
--   language_preference   text
--   desired_value         text
--   practice_time         text
--   tradition_detail      text | null
--   completed_at          timestamptz | null
--   version               text  -- e.g. "cfb_june_2026"
--
-- RLS is unchanged: users_manage_own_profile already covers the row.
-- Backend service role (Railway) bypasses RLS via direct Postgres URL.

alter table public.user_profile
  add column if not exists ben_onboarding jsonb;
