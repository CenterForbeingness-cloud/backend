-- Run once in Supabase SQL editor if admin_audit_log already exists without ADMIN_ROLE_CHANGE.
ALTER TABLE public.admin_audit_log DROP CONSTRAINT IF EXISTS admin_audit_log_action_check;
ALTER TABLE public.admin_audit_log ADD CONSTRAINT admin_audit_log_action_check CHECK (
    action IN (
        'CREATE_COURSE', 'UPDATE_COURSE', 'DELETE_COURSE', 'PUBLISH_COURSE',
        'CREATE_WEEK', 'UPDATE_WEEK', 'DELETE_WEEK',
        'CREATE_LESSON', 'UPDATE_LESSON', 'DELETE_LESSON',
        'GRANT_ENTITLEMENT', 'REVOKE_ENTITLEMENT',
        'ADMIN_LOGIN', 'ADMIN_LOGOUT', 'ADMIN_PASSWORD_CHANGE',
        'ADMIN_ROLE_CHANGE',
        'ADMIN_USER_CREATE', 'ADMIN_USER_DEACTIVATE', 'ADMIN_USER_ACTIVATE', 'ADMIN_USER_DELETE',
        'ADMIN_2FA_ENABLE', 'ADMIN_2FA_DISABLE',
        'VIEW_ANALYTICS', 'EXPORT_DATA'
    )
);
