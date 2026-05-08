alter table if exists public.chat_sessions
  add column if not exists user_id uuid references auth.users(id) on delete cascade;

create index if not exists idx_chat_sessions_user_updated_at
  on public.chat_sessions(user_id, updated_at desc);

create index if not exists idx_chat_messages_session_created_at
  on public.chat_messages(session_id, created_at, id);

alter table public.chat_sessions enable row level security;
alter table public.chat_messages enable row level security;

drop policy if exists "users_manage_own_chat_sessions" on public.chat_sessions;
create policy "users_manage_own_chat_sessions"
on public.chat_sessions
for all
to authenticated
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

drop policy if exists "users_manage_own_chat_messages" on public.chat_messages;
create policy "users_manage_own_chat_messages"
on public.chat_messages
for all
to authenticated
using (
  exists (
    select 1
    from public.chat_sessions s
    where s.session_id = chat_messages.session_id
      and s.user_id = auth.uid()
  )
)
with check (
  exists (
    select 1
    from public.chat_sessions s
    where s.session_id = chat_messages.session_id
      and s.user_id = auth.uid()
  )
);