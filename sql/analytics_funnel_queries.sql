-- Gate 4: launch funnel + voice engagement (run in Supabase SQL editor)
-- Requires: public.analytics_events (supabase_analytics_events.sql)

-- Adjust window:
--   now() - interval '7 days'   weekly ops
--   now() - interval '30 days'  monthly review

-- 1) Funnel — unique users per step (last 7 days)
SELECT event_name, COUNT(DISTINCT user_id) AS unique_users
FROM public.analytics_events
WHERE created_at >= now() - interval '7 days'
  AND user_id IS NOT NULL
  AND event_name IN (
    'signup_complete',
    'first_chat_message',
    'voice_session_start',
    'checkout_started',
    'purchase_completed'
  )
GROUP BY event_name
ORDER BY unique_users DESC;

-- 2) Conversion rate (signup → purchase, same window)
WITH funnel AS (
  SELECT
    COUNT(DISTINCT user_id) FILTER (WHERE event_name = 'signup_complete') AS signups,
    COUNT(DISTINCT user_id) FILTER (WHERE event_name = 'purchase_completed') AS purchases
  FROM public.analytics_events
  WHERE created_at >= now() - interval '7 days'
    AND user_id IS NOT NULL
)
SELECT
  signups,
  purchases,
  CASE WHEN signups > 0 THEN round(100.0 * purchases / signups, 1) END AS conversion_pct
FROM funnel;

-- 3) Voice — total spoken seconds + users with voice (last 7 days)
SELECT
  COUNT(DISTINCT user_id) AS voice_users,
  COALESCE(SUM((properties->>'spoken_seconds')::numeric), 0) AS spoken_seconds_total,
  CASE
    WHEN COUNT(DISTINCT user_id) > 0 THEN
      round(
        COALESCE(SUM((properties->>'spoken_seconds')::numeric), 0)
        / COUNT(DISTINCT user_id),
        1
      )
  END AS avg_spoken_seconds_per_voice_user
FROM public.analytics_events
WHERE event_name = 'voice_session_end'
  AND created_at >= now() - interval '7 days'
  AND user_id IS NOT NULL;

-- 4) RAG health — miss rate (last 7 days)
SELECT
  COUNT(*) FILTER (WHERE event_name = 'rag_retrieval') AS hits,
  COUNT(*) FILTER (WHERE event_name = 'rag_retrieval_miss') AS misses,
  CASE
    WHEN COUNT(*) FILTER (WHERE event_name IN ('rag_retrieval', 'rag_retrieval_miss')) > 0
    THEN round(
      100.0 * COUNT(*) FILTER (WHERE event_name = 'rag_retrieval_miss')
      / COUNT(*) FILTER (WHERE event_name IN ('rag_retrieval', 'rag_retrieval_miss')),
      1
    )
  END AS miss_rate_pct
FROM public.analytics_events
WHERE created_at >= now() - interval '7 days'
  AND event_name IN ('rag_retrieval', 'rag_retrieval_miss');

-- 5) Daily event volume (spot trends)
SELECT date_trunc('day', created_at AT TIME ZONE 'UTC') AS day, event_name, COUNT(*) AS events
FROM public.analytics_events
WHERE created_at >= now() - interval '14 days'
GROUP BY 1, 2
ORDER BY 1 DESC, 3 DESC;

-- Revenue: use Stripe Dashboard; reconcile purchase_completed count with charges.
