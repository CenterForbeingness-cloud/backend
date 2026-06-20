-- Theme-based daily schedule for week-zero-reset (guide mode — not verbatim script)
-- Run AFTER: supabase_course_daily_schedule.sql + seed_courses_example.sql

begin;

delete from public.course_daily_schedule
where course_slug = 'week-zero-reset';

insert into public.course_daily_schedule (course_slug, day_number, day_title, content)
values
  (
    'week-zero-reset',
    1,
    'Witness and arrival',
    E'Day 1 themes — guide in your own words:\n'
    || E'- Check in: how are they arriving right now?\n'
    || E'- Introduce the witness: watching the breath, noticing awareness of watching\n'
    || E'- Keep it about 10 minutes unless they want shorter\n'
    || E'- When complete, invite them to tap Complete day in the app'
  ),
  (
    'week-zero-reset',
    2,
    'Energy and the body',
    E'Day 2 themes:\n'
    || E'- Brief check-in on body and energy\n'
    || E'- Gentle body scan or felt sense of aliveness\n'
    || E'- Connect to course teaching on moving energy when relevant (RAG)\n'
    || E'- Invite Complete day when the sit feels finished'
  ),
  (
    'week-zero-reset',
    3,
    'Self-acceptance and integration',
    E'Day 3 themes:\n'
    || E'- What are they carrying today?\n'
    || E'- Practice of allowing / self-acceptance without fixing\n'
    || E'- Short closing: one thing to carry into the rest of the day\n'
    || E'- Invite Complete day when ready'
  );

commit;

-- Verify:
-- select day_number, day_title, left(content, 80) from public.course_daily_schedule
-- where course_slug = 'week-zero-reset' order by day_number;
