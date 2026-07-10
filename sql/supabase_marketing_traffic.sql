-- Phase 2: marketing page-view rollups (beacon from Sentaint Web → Railway backend)

CREATE TABLE IF NOT EXISTS public.marketing_beacon_dedup (
  stat_date date NOT NULL,
  page_path text NOT NULL DEFAULT '/',
  session_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (stat_date, page_path, session_id)
);

CREATE TABLE IF NOT EXISTS public.marketing_daily_rollups (
  stat_date date NOT NULL,
  page_path text NOT NULL DEFAULT '/',
  page_views integer NOT NULL DEFAULT 0,
  unique_sessions integer NOT NULL DEFAULT 0,
  PRIMARY KEY (stat_date, page_path)
);

CREATE INDEX IF NOT EXISTS marketing_daily_rollups_stat_date_idx
  ON public.marketing_daily_rollups (stat_date DESC);

ALTER TABLE public.marketing_beacon_dedup ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.marketing_daily_rollups ENABLE ROW LEVEL SECURITY;
