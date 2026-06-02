-- MVP Launch: ElevenLabs (or other) voice ID per course
-- Populate manually after voice clone registration.

create table if not exists public.course_voice_profiles (
  course_slug text primary key,
  provider text not null default 'elevenlabs',
  voice_id text not null,
  reference_audio_manifest jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.course_voice_profiles enable row level security;

-- Backend service role reads; no client access required at MVP.
drop policy if exists course_voice_profiles_select_service on public.course_voice_profiles;
create policy course_voice_profiles_select_service
  on public.course_voice_profiles
  for select
  to service_role
  using (true);
