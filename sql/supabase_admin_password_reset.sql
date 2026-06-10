-- Admin self-service password reset (run once in Supabase SQL editor).

ALTER TABLE public.admin_users
  ADD COLUMN IF NOT EXISTS reset_token_hash TEXT,
  ADD COLUMN IF NOT EXISTS reset_expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_admin_users_reset_token_hash
  ON public.admin_users (reset_token_hash)
  WHERE reset_token_hash IS NOT NULL;
