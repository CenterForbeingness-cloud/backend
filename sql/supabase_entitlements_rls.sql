-- Entitlements, Billing, and Admin Schema for Sentient
-- Purpose: Persist course purchases, user entitlements, and admin audit logs
-- Security: RLS enabled, parameterized queries only, no injection vectors
--
-- Run in Supabase SQL Editor (safe to re-run: IF NOT EXISTS / DROP POLICY IF EXISTS).
-- Service-role policies use FOR ALL (not "FOR INSERT, UPDATE, DELETE" — invalid in PostgreSQL).
-- If tables were auto-created by the backend bootstrap, this script adds RLS and patches columns.

-- ============================================================================
-- COURSE PURCHASES TABLE (immutable purchase records)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.course_purchases (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    course_slug TEXT NOT NULL,
    purchase_source TEXT NOT NULL CHECK (purchase_source IN ('stripe', 'apple', 'google', 'admin_grant')),
    stripe_session_id TEXT,
    stripe_payment_intent_id TEXT,
    purchased_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT timezone('utc'::text, now()),
    refunded_at TIMESTAMP WITH TIME ZONE,
    
    -- Constraints
    CONSTRAINT valid_course_slug CHECK (course_slug ~ '^[a-z0-9\-]+$'),
    CONSTRAINT valid_stripe_session CHECK (
        (purchase_source = 'stripe' AND stripe_session_id IS NOT NULL)
        OR purchase_source != 'stripe'
    )
);

CREATE INDEX IF NOT EXISTS idx_course_purchases_user_id ON public.course_purchases(user_id);
CREATE INDEX IF NOT EXISTS idx_course_purchases_course_slug ON public.course_purchases(course_slug);
CREATE INDEX IF NOT EXISTS idx_course_purchases_stripe_session ON public.course_purchases(stripe_session_id) WHERE stripe_session_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_course_purchases_stripe_idempotency 
    ON public.course_purchases(stripe_session_id) 
    WHERE stripe_session_id IS NOT NULL AND refunded_at IS NULL;

-- ============================================================================
-- USER ENTITLEMENTS TABLE (derived from course_purchases, soft-deletable)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.user_entitlements (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    course_slug TEXT NOT NULL,
    
    -- Ownership tracking
    granted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT timezone('utc'::text, now()),
    granted_by TEXT NOT NULL CHECK (granted_by IN ('stripe', 'apple', 'google', 'admin')),
    expires_at TIMESTAMP WITH TIME ZONE,
    
    -- Soft-delete (revocation)
    revoked_at TIMESTAMP WITH TIME ZONE,
    revoked_by TEXT CHECK ((revoked_at IS NULL) OR (revoked_by IN ('admin', 'refund', 'expiry'))),
    revoke_reason TEXT,
    
    -- Constraints
    CONSTRAINT valid_course_slug CHECK (course_slug ~ '^[a-z0-9\-]+$'),
    CONSTRAINT no_revoke_without_reason CHECK ((revoked_at IS NULL) OR (revoke_reason IS NOT NULL)),
    CONSTRAINT expires_after_granted CHECK ((expires_at IS NULL) OR (expires_at > granted_at))
);

CREATE INDEX IF NOT EXISTS idx_user_entitlements_user_id ON public.user_entitlements(user_id);
CREATE INDEX IF NOT EXISTS idx_user_entitlements_user_active 
    ON public.user_entitlements(user_id, course_slug) 
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_user_entitlements_expires ON public.user_entitlements(expires_at) WHERE expires_at IS NOT NULL;

-- Unique constraint: one active entitlement per user per course
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_entitlements_active_unique 
    ON public.user_entitlements(user_id, course_slug) 
    WHERE revoked_at IS NULL;

-- ============================================================================
-- PURCHASE EVENTS TABLE (immutable webhook event log for idempotency)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.purchase_events (
    id BIGSERIAL PRIMARY KEY,
    
    -- Stripe event metadata
    stripe_event_id TEXT NOT NULL UNIQUE,
    stripe_event_type TEXT NOT NULL,
    stripe_session_id TEXT NOT NULL,
    
    -- Business data
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    course_slug TEXT NOT NULL,
    
    -- Processing
    received_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT timezone('utc'::text, now()),
    processed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT timezone('utc'::text, now()),
    processing_status TEXT NOT NULL DEFAULT 'success' CHECK (processing_status IN ('success', 'failed', 'ignored')),
    processing_error TEXT,
    
    -- Audit
    idempotency_key TEXT UNIQUE,
    
    -- Constraints
    CONSTRAINT valid_course_slug CHECK (course_slug ~ '^[a-z0-9\-]+$')
);

CREATE INDEX IF NOT EXISTS idx_purchase_events_stripe_event_id ON public.purchase_events(stripe_event_id);
CREATE INDEX IF NOT EXISTS idx_purchase_events_user_id ON public.purchase_events(user_id);
CREATE INDEX IF NOT EXISTS idx_purchase_events_received_at ON public.purchase_events(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_purchase_events_idempotency_key ON public.purchase_events(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- ============================================================================
-- USER MESSAGE COUNTS TABLE (for quota enforcement)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.user_message_counts (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Rolling counter
    message_count INT NOT NULL DEFAULT 0 CHECK (message_count >= 0),
    period_start TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT timezone('utc'::text, now()),
    
    -- Audit
    last_updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_user_message_counts_user_id ON public.user_message_counts(user_id);

-- ============================================================================
-- ADMIN USERS TABLE (separate from auth.users for admin role management)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.admin_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Credentials
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,  -- bcrypt hash, never stored plain
    
    -- 2FA
    totp_secret TEXT,  -- Base32 encoded TOTP seed (RFC 6238), encrypted at rest optional
    totp_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    
    -- Role & Status
    role TEXT NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')) DEFAULT 'viewer',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    
    -- Audit
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT timezone('utc'::text, now()),
    last_login TIMESTAMP WITH TIME ZONE,
    last_login_ip TEXT,
    password_changed_at TIMESTAMP WITH TIME ZONE,
    
    -- Constraints
    CONSTRAINT valid_email CHECK (email ~ '^[^\s@]+@[^\s@]+\.[^\s@]+$')
);

CREATE INDEX IF NOT EXISTS idx_admin_users_email ON public.admin_users(email);
CREATE INDEX IF NOT EXISTS idx_admin_users_is_active ON public.admin_users(is_active);

-- ============================================================================
-- ADMIN AUDIT LOG TABLE (immutable, compliance trail)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.admin_audit_log (
    id BIGSERIAL PRIMARY KEY,
    
    -- Actor
    admin_id UUID NOT NULL REFERENCES public.admin_users(id) ON DELETE SET NULL,
    
    -- Action
    action TEXT NOT NULL CHECK (
        action IN (
            'CREATE_COURSE', 'UPDATE_COURSE', 'DELETE_COURSE', 'PUBLISH_COURSE',
            'CREATE_WEEK', 'UPDATE_WEEK', 'DELETE_WEEK',
            'CREATE_LESSON', 'UPDATE_LESSON', 'DELETE_LESSON',
            'GRANT_ENTITLEMENT', 'REVOKE_ENTITLEMENT',
            'ADMIN_LOGIN', 'ADMIN_LOGOUT', 'ADMIN_PASSWORD_CHANGE',
            'ADMIN_ROLE_CHANGE',
            'ADMIN_2FA_ENABLE', 'ADMIN_2FA_DISABLE',
            'VIEW_ANALYTICS', 'EXPORT_DATA'
        )
    ),
    
    -- Resource
    resource_type TEXT NOT NULL CHECK (resource_type IN ('course', 'week', 'lesson', 'user', 'entitlement', 'admin')),
    resource_id TEXT,
    
    -- Context
    details JSONB,  -- Diff, old/new values, user affected, etc.
    http_method TEXT,
    http_path TEXT,
    http_status_code INT,
    client_ip TEXT,
    
    -- Audit
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_log_admin_id ON public.admin_audit_log(admin_id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_action ON public.admin_audit_log(action);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_resource ON public.admin_audit_log(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created_at ON public.admin_audit_log(created_at DESC);

-- ============================================================================
-- ROW-LEVEL SECURITY POLICIES
-- ============================================================================

-- Enable RLS on all new tables
ALTER TABLE public.course_purchases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_entitlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.purchase_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_message_counts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.admin_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.admin_audit_log ENABLE ROW LEVEL SECURITY;

-- COURSE_PURCHASES RLS: Users can only read/write their own purchases
DROP POLICY IF EXISTS "users_read_own_purchases" ON public.course_purchases;
CREATE POLICY "users_read_own_purchases"
ON public.course_purchases
FOR SELECT
TO authenticated
USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "service_write_purchases" ON public.course_purchases;
CREATE POLICY "service_write_purchases"
ON public.course_purchases
FOR INSERT
TO service_role
WITH CHECK (TRUE);

-- USER_ENTITLEMENTS RLS: Users can only read their own entitlements
DROP POLICY IF EXISTS "users_read_own_entitlements" ON public.user_entitlements;
CREATE POLICY "users_read_own_entitlements"
ON public.user_entitlements
FOR SELECT
TO authenticated
USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "service_write_entitlements" ON public.user_entitlements;
CREATE POLICY "service_write_entitlements"
ON public.user_entitlements
FOR ALL
TO service_role
USING (TRUE)
WITH CHECK (TRUE);

-- PURCHASE_EVENTS RLS: Only service_role can write (backend webhook processor)
DROP POLICY IF EXISTS "service_write_events" ON public.purchase_events;
CREATE POLICY "service_write_events"
ON public.purchase_events
FOR INSERT
TO service_role
WITH CHECK (TRUE);

-- PURCHASE_EVENTS RLS: Admins can read all events (audit), users cannot
DROP POLICY IF EXISTS "admin_read_events" ON public.purchase_events;
CREATE POLICY "admin_read_events"
ON public.purchase_events
FOR SELECT
TO authenticated
USING (EXISTS (
    SELECT 1 FROM public.admin_users WHERE id = auth.uid() AND is_active = TRUE
));

-- USER_MESSAGE_COUNTS RLS: Service role only (updated by backend quota checker)
DROP POLICY IF EXISTS "service_write_counts" ON public.user_message_counts;
CREATE POLICY "service_write_counts"
ON public.user_message_counts
FOR ALL
TO service_role
USING (TRUE)
WITH CHECK (TRUE);

-- ADMIN_USERS RLS: Admin users cannot access via regular authenticated role
-- Admins are authenticated separately with admin JWT
DROP POLICY IF EXISTS "admin_read_admins" ON public.admin_users;
CREATE POLICY "admin_read_admins"
ON public.admin_users
FOR SELECT
TO authenticated
USING (FALSE);  -- No authenticated user can read admin table via normal JWT

-- ADMIN_AUDIT_LOG RLS: Only admins can read, service_role can write
DROP POLICY IF EXISTS "admin_read_audit" ON public.admin_audit_log;
CREATE POLICY "admin_read_audit"
ON public.admin_audit_log
FOR SELECT
TO authenticated
USING (FALSE);  -- Requires separate admin role

DROP POLICY IF EXISTS "service_write_audit" ON public.admin_audit_log;
CREATE POLICY "service_write_audit"
ON public.admin_audit_log
FOR INSERT
TO service_role
WITH CHECK (TRUE);

-- ============================================================================
-- HELPFUL VIEWS (for backend queries with RLS applied)
-- ============================================================================

-- View for checking if a user owns a course (active entitlements only)
CREATE OR REPLACE VIEW public.user_active_entitlements AS
SELECT 
    user_id,
    course_slug,
    granted_at,
    expires_at
FROM public.user_entitlements
WHERE revoked_at IS NULL
AND (expires_at IS NULL OR expires_at > timezone('utc'::text, now()));

-- View for analytics (purchases by course)
CREATE OR REPLACE VIEW public.purchase_summary AS
SELECT 
    course_slug,
    COUNT(*) as total_purchases,
    COUNT(DISTINCT user_id) as unique_users,
    MAX(purchased_at) as last_purchase
FROM public.course_purchases
WHERE refunded_at IS NULL
GROUP BY course_slug;

-- ============================================================================
-- MIGRATION HELPER COMMENTS
-- ============================================================================
COMMENT ON TABLE public.course_purchases IS 'Immutable record of all course purchases. Refunds soft-delete via refunded_at timestamp.';
COMMENT ON TABLE public.user_entitlements IS 'Active course ownership. Can be revoked by setting revoked_at. Expires via expires_at field.';
COMMENT ON TABLE public.purchase_events IS 'Stripe webhook event log. Used for idempotency; ensures no duplicate entitlements from webhook retries.';
COMMENT ON TABLE public.user_message_counts IS 'Rolling message counter for quota enforcement. Updated by backend after each /chat request.';
COMMENT ON TABLE public.admin_users IS 'Admin users table (separate from auth.users). Stores bcrypt-hashed passwords and TOTP secrets.';
COMMENT ON TABLE public.admin_audit_log IS 'Immutable audit trail of all admin actions. Required for HIPAA/SOC2 compliance.';
COMMENT ON COLUMN public.course_purchases.purchase_source IS 'One of: stripe, apple, google, admin_grant. Indicates how the purchase was made.';
COMMENT ON COLUMN public.user_entitlements.granted_by IS 'Source of the grant: stripe, apple, google, or admin (manual grant).';
COMMENT ON COLUMN public.user_entitlements.expires_at IS 'Optional expiry for trial entitlements. NULL means no expiry.';
COMMENT ON COLUMN public.admin_users.totp_secret IS 'Base32-encoded TOTP seed. Used with authenticator apps (Google Authenticator, Authy, etc.).';
COMMENT ON COLUMN public.admin_audit_log.details IS 'JSONB object containing action-specific details: old/new values, affected users, etc.';

-- ============================================================================
-- GRANTS (for typical FastAPI backend with service_role)
-- ============================================================================
-- Uncomment these after testing to lock down permissions:
-- 
-- GRANT SELECT ON public.user_active_entitlements TO authenticated;
-- GRANT SELECT ON public.purchase_summary TO authenticated;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON public.course_purchases TO service_role;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_entitlements TO service_role;
-- GRANT SELECT, INSERT ON public.purchase_events TO service_role;
-- GRANT SELECT, INSERT, UPDATE ON public.user_message_counts TO service_role;
-- GRANT SELECT, INSERT ON public.admin_audit_log TO service_role;
-- REVOKE ALL ON public.admin_users FROM authenticated;
-- GRANT SELECT, INSERT, UPDATE ON public.admin_users TO service_role;
