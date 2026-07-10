-- Run once if waitlist_signups already exists. Prevents duplicate emails (case/whitespace variants).

-- 1) Normalize existing rows
UPDATE public.waitlist_signups
SET email = lower(trim(email))
WHERE email IS NOT NULL AND email <> lower(trim(email));

-- 2) Resolve exact duplicate addresses (keep earliest signup)
-- If this DELETE affects rows, review in Supabase before re-running index step.
DELETE FROM public.waitlist_signups a
USING public.waitlist_signups b
WHERE lower(trim(a.email)) = lower(trim(b.email))
  AND a.created_at > b.created_at;

DELETE FROM public.waitlist_signups a
USING public.waitlist_signups b
WHERE lower(trim(a.email)) = lower(trim(b.email))
  AND a.id > b.id
  AND a.created_at = b.created_at;

-- 3) Enforce one row per normalized email
CREATE UNIQUE INDEX IF NOT EXISTS waitlist_signups_email_normalized_uidx
  ON public.waitlist_signups (lower(trim(email)));
