-- =============================================================================
-- WIPE ALL DATA (keep tables / schema)
-- =============================================================================
-- WARNING: Irreversible. Deletes every row in public tables and resets
-- serial/identity sequences. Table definitions, RLS, indexes, and functions stay.
--
-- Run in the Supabase SQL editor (or any Postgres session with enough privileges).
-- Prefer a non-production project unless you really mean it.
-- =============================================================================

begin;

-- Truncate every base table in public in one shot (handles FKs via CASCADE).
do $$
declare
  stmt text;
begin
  select
    'truncate table '
    || string_agg(format('%I.%I', schemaname, tablename), ', ')
    || ' restart identity cascade'
  into stmt
  from pg_tables
  where schemaname = 'public'
    and tableowner is not null;

  if stmt is null then
    raise notice 'No public tables found — nothing to truncate.';
  else
    raise notice 'Executing: %', stmt;
    execute stmt;
  end if;
end $$;

-- Optional: also wipe Supabase Auth users (app accounts).
-- Uncomment the block below if you want auth.users emptied too.
-- This cascades to public rows that reference auth.users(id).
/*
truncate table
  auth.identities,
  auth.sessions,
  auth.refresh_tokens,
  auth.mfa_factors,
  auth.mfa_challenges,
  auth.mfa_amr_claims,
  auth.one_time_tokens,
  auth.users
restart identity cascade;
*/

commit;
