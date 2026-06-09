-- Admin invite + self-service 2FA setup (run once in Supabase SQL editor).

ALTER TABLE public.admin_users
  ADD COLUMN IF NOT EXISTS invite_token_hash TEXT,
  ADD COLUMN IF NOT EXISTS invite_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS setup_completed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_admin_users_invite_token_hash
  ON public.admin_users (invite_token_hash)
  WHERE invite_token_hash IS NOT NULL;

-- Invited admins finish setup before totp_enabled becomes true.
ALTER TABLE public.admin_users
  ALTER COLUMN totp_enabled SET DEFAULT FALSE;
