-- Marketing site waitlist (same as Sentaint Web/supabase/migrations/001_waitlist_signups.sql)
-- Run once in Supabase SQL editor when using one Supabase project for app + website.

CREATE TABLE IF NOT EXISTS public.waitlist_signups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL,
  source text NOT NULL DEFAULT 'sentient-landing',
  created_at timestamptz NOT NULL DEFAULT now(),
  launch_notified_at timestamptz,
  ip_hash text,
  CONSTRAINT waitlist_signups_email_key UNIQUE (email)
);

CREATE INDEX IF NOT EXISTS waitlist_signups_launch_pending_idx
  ON public.waitlist_signups (created_at)
  WHERE launch_notified_at IS NULL;

ALTER TABLE public.waitlist_signups ENABLE ROW LEVEL SECURITY;

-- No public policies: backend DB URL / service role only.
