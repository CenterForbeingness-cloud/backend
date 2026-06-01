# Sentient Backend

FastAPI backend for the AI chat companion, billing, course context (RAG), and sessions.

**Status (May 2026):** See [`BACKEND_8_10_SUMMARY.md`](../BACKEND_8_10_SUMMARY.md) for the honest split:

| | Score |
|---|-------|
| Infrastructure (auth, chat, Stripe, entitlements, RAG, deploy) | **~9/10** |
| Product moat (profile, memory injection, facts, goals, check-ins) | **~2/10** |

**Build order:** [`MVP_NORTH_STAR.md`](../MVP_NORTH_STAR.md) — next backend work is **`user_profile` + inject into `/chat` prompts**, not admin UI or voice.

**Launch gates:** [`SECURITY_AND_REMAINING_WORK.md`](../SECURITY_AND_REMAINING_WORK.md)

---

## Current Backend Layout

- `app/main.py` API routes
- `app/models.py` request/response schema
- `app/ai.py` OpenAI/Claude routing and safe fallback replies
- `app/storage.py` storage abstraction (in-memory or Postgres)
- `app/daily_schedule.py` daily schedule parse, import, and chat context
- `app/course_progress.py` per-user current day for schedule courses
- `app/config.py` app/environment configuration
- `scripts/import_daily_schedule.py` import `schedules/*.txt` into Postgres

## Architecture Note

Backend is deployed as a separate service from the Flutter app.
Flutter (Dart) connects to this backend over HTTP/WebSocket APIs.
Keep frontend and backend release pipelines independent.

For deploying from this monorepo, splitting into a second Git repo, and staging vs production readiness, see **`docs/BACKEND_DEPLOYMENT_AND_REPO_SPLIT.md`**.

Companion-first product direction:

- `/chat` is the main user-facing interface (not “open course first”).
- **Not implemented yet:** `user_profile`, memory injection, `user_facts`, `user_goals`, `memory_events`, `checkins` — see `MVP_NORTH_STAR.md`.
- Courses are **context for the AI** (RAG + entitlements), not the product shell.
- Infrastructure (billing, webhooks, quotas, admin login API) is largely done; differentiation is memory.

## Endpoints

- `GET /health`
- `POST /chat`
- `POST /chat/stream` (SSE; scripted daily lessons + streamed LLM for other modes)
- `POST /sessions`
- `GET /sessions/{session_id}/messages?limit=50`
- `DELETE /memory/{session_id}`
- `POST /billing/webhook`
- `POST /billing/checkout`
- `POST /billing/payment-intent`
- `GET /courses`
- `GET /courses/{course_slug}`
- `GET /courses/{course_slug}/progress`
- `POST /courses/{course_slug}/progress/advance`
- `GET /entitlements`
- `GET /usage`
- `GET /profile` — thin companion memory (Phase 1)
- `PATCH /profile` — update goals / focus

`POST /sessions` accepts an optional `session_id` and returns the active session id.
`GET /sessions/{session_id}/messages` returns recent messages for that session.

## Storage

Set `SUPABASE_DB_URL` to your Supabase Postgres connection string to persist chat history.
If it is omitted, the backend falls back to in-memory session storage.

If `SUPABASE_DB_URL` is set but invalid/unreachable, startup falls back to in-memory storage.

When Postgres is enabled, the API uses a **connection pool** (`app/db.py`, started on app startup) so `/chat` does not open a new database connection for every query.

## Chat performance

Daily practice was slow (~20s) mainly due to many sequential Supabase round-trips and a large system prompt. Recent changes:

- Pooled Postgres connections
- Cached schedule days and max day per course
- Single DB transaction per message append
- Background quota and progress timestamp updates
- Smaller prompts for daily lessons (no base script duplicate, 6-message history cap)

See **`docs/CHAT_PERFORMANCE.md`** for targets, measurement commands, and the remaining roadmap (streaming, regional deploy).

Optional env:

- `SCHEDULE_HISTORY_MESSAGES` (default `6`)
- `CHAT_MODEL` / `CHAT_MODEL_SCHEDULE` (default `gpt-4o-mini`)

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies:
   `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and add keys if needed.
4. Run:
   `python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

## Startup Procedure (Windows PowerShell)

1. Open terminal in backend folder:
   `cd c:\Users\thoma\Desktop\Sentient\backend`
2. Create venv (first time only):
   `python -m venv .venv`
3. Activate venv:
   `.\.venv\Scripts\Activate.ps1`
4. Install packages (first time or after updates):
   `pip install -r requirements.txt`
5. Create env file (first time only):
   `copy .env.example .env`
6. Start API server:
   `python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

If port 8000 is unavailable on your machine:
`python -m uvicorn app.main:app --host 0.0.0.0 --port 8001`

## Startup Procedure (macOS / zsh)

1. Open Terminal and go to backend folder:
   `cd ~/Desktop/Sentient-main/backend`
2. Create venv (first time only):
   `python3 -m venv venv`
3. Activate venv:
   `source venv/bin/activate`
4. Install packages (first time or after updates):
   `python -m pip install -r requirements.txt`
5. Create env file (first time only):
   `cp .env.example .env`
6. Start API server:
   `python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

If port 8000 is unavailable on your machine:
`python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001`

If `python3` is not in PATH on Apple Silicon/Homebrew setups, this command also works:
`/opt/homebrew/bin/python3 -m venv venv`

## Verify It Is Running

- Root: `http://127.0.0.1:8000/`
- Health: `http://127.0.0.1:8000/health`
- Docs: `http://127.0.0.1:8000/docs`

If using port 8001, replace `8000` with `8001` in the URLs above.

## Chat Request Example

```json
{
  "session_id": "user-123",
  "message": "I feel stressed today",
  "provider": "openai"
}
```

If API keys are missing, backend returns a fallback echo so you can test app wiring first.

If provider requests fail (for example quota/auth issues), backend also returns MVP fallback text instead of HTTP 500.

## RAG Scaffold

The backend includes a retriever seam at `app/rag.py` used by `/chat`.
It currently defaults to a no-op retriever, so behavior is unchanged until you plug in Pinecone/Weaviate.

To add retrieval later, implement `retrieve(query, top_k)` in a retriever class and return context chunks.
Those chunks are injected into the system prompt in `app/ai.py`.

Environment flags for scaffold control:
- `RAG_ENABLED=false`
- `RAG_TOP_K=3`

## Course Catalog And Billing

The backend course catalog is still available, but it now serves the broader companion experience as support content rather than the app's primary product.

The backend course catalog now prefers the Supabase course tables when `SUPABASE_DB_URL` is configured.
If that database connection is unavailable, it falls back to the local `rag/raw/courses/` directory so development still works.

The pricing flow should remain course-aware, but the product story is companion-first:

- `GET /courses` returns published course metadata plus pricing fields when present in the database.
- `GET /entitlements` returns the authenticated user's owned course slugs.
- `POST /billing/payment-intent` and `POST /billing/checkout` both accept a `course_slug` so the backend can attach purchase metadata.
- Future pricing tiers may also cover companion features such as advanced memory, check-ins, summaries, and voice.
- The frontend pricing screen can render both companion tiers and course support content as the product evolves.

For Supabase deployments, the relevant schema is documented in `backend/sql/supabase_courses_billing_rls.sql`.

For persistent chat, add a direct Postgres URL in `.env`:

`SUPABASE_DB_URL=postgresql://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres?sslmode=require`

SQL setup files:

- `backend/sql/supabase_chat_rls.sql` for chat session/message schema and RLS
- `backend/sql/supabase_entitlements_rls.sql` for **`course_purchases`**, **`user_entitlements`**, **`purchase_events`**, and RLS (run after local dev bootstrap or on fresh Supabase)
- `backend/sql/supabase_courses_billing_rls.sql` for course catalog (`course_weeks`, `course_products`, etc.) — optional; filesystem fallback works without it
- `backend/sql/supabase_course_daily_schedule.sql` for day-by-day course schedule rows (imported from external text files)
- `backend/sql/supabase_user_course_progress.sql` for per-user current day in a schedule course

Billing status (May 2026): webhooks grant ownership into Postgres; `GET /entitlements` drives Owned/Locked UI; starter bundle PaymentSheet E2E and ownership sync are verified in dev. Prepared-statement pooler errors were addressed by disabling server-side prepares in DB connections. See `STRIPE_PAYMENTS.md` and `PROGRESS_MAY_27_2026.md`.

## Daily Course Schedule

Day-by-day course copy lives in **Postgres** (`course_daily_schedule`), not in `rag/raw/`. Source `.txt` files live in `schedules/` at the repo root and are imported once; the app does not read those files at runtime.

This path is **independent** of the week/lesson catalog and from Pinecone transcript ingest. See **`schedules/README.md`** for the full step-by-step guide.

### Schema

| Table | Purpose |
|-------|---------|
| `public.courses` | One row per course (`course_slug` PK). Created by the schedule migration if missing, or by `supabase_courses_billing_rls.sql`. |
| `public.course_daily_schedule` | One row per day: `course_slug`, `day_number`, optional `day_title`, `content`. |

Migration file: `backend/sql/supabase_course_daily_schedule.sql` (safe to run standalone in Supabase SQL editor).

### Setup checklist

1. Run `backend/sql/supabase_course_daily_schedule.sql` in Supabase.
2. Run `backend/sql/supabase_user_course_progress.sql`.
3. Set `SUPABASE_DB_URL` in `backend/.env`.
4. Insert a catalog row, e.g. `mindful-foundations` (SQL in `schedules/README.md`).
5. Import from `backend/`:

   ```bash
   python scripts/import_daily_schedule.py --course-slug mindful-foundations --file ../schedules/mindful-foundations.example.txt
   ```

6. Confirm rows: `SELECT * FROM public.course_daily_schedule WHERE course_slug = 'mindful-foundations' ORDER BY day_number;`

### Schedule days vs calendar dates

`current_day_number` is the **schedule index** (`Day 1`, `Day 2`, … in the imported file), not “today’s date.” Users stay on day 2 until `POST /courses/{slug}/progress/advance` (or an explicit `day_number` on `/chat`). Calendar-based auto-advance is not implemented; see `schedules/README.md`.

### Chat behavior

With `course_slug` and an authenticated entitled user, the backend resolves the schedule day from `user_course_progress` (default day 1 on first chat). Optional `day_number` on the request overrides stored progress.

The chat response includes `day_number` — the day injected into context.

```json
{
  "session_id": "session-abc",
  "message": "What should I focus on today?",
  "course_slug": "mindful-foundations"
}
```

Advance after the user completes a day:

`POST /courses/{course_slug}/progress/advance`

Daily practice chat uses a dedicated coaching block in the system prompt (today's lesson script, short replies, move into the exercise after check-in). Pinecone is skipped for daily-only chat so transcript chunks do not override the schedule. Week/lesson chat still uses Pinecone when `week_number` is set.

### Code

- `app/daily_schedule.py` — parse, `replace_schedule`, `get_schedule_day`
- `app/course_progress.py` — `resolve_schedule_day_number`, `get_progress`, `advance_day`
- `scripts/import_daily_schedule.py` — CLI import (replaces all days for a slug)

Stripe env variables expected by backend config:

- `STRIPE_SECRET_KEY` (server-side Stripe secret key)
- `STRIPE_PUBLISHABLE_KEY` (public key, returned to clients by future billing endpoints)
- `STRIPE_WEBHOOK_SECRET` (webhook signature verification)

Backward compatibility:

- `STRIPE_API_KEY` is still supported as a fallback alias for `STRIPE_SECRET_KEY`.

Stripe CLI and webhook testing guide:

- `backend/STRIPE_PAYMENTS.md`

## Next backend work (priority order)

### Phase 1 — highest ROI (moat)

1. **`user_profile`** — SQL migration + RLS + `GET`/`PATCH /profile`
2. **Prompt injection** — load profile in `chat_service.py`; add `USER PROFILE` block to system prompt (`primary_goal`, `current_focus`, `biggest_obstacle`, `motivation_type`)
3. **Onboarding or first-chat capture** — write profile row (manual fields OK; no extraction engine required for v1)

### Phase 1 — launch hardening (parallel)

- Prod env: `AUTH_ENFORCED`, `CHAT_TOKEN_ENFORCED`, `RATE_LIMIT_ENABLED`, live Stripe webhook, CORS, Supabase redirects (`SECURITY_AND_REMAINING_WORK.md`)

### Phase 2 — memory+

- `user_facts`, `user_goals`, `memory_events`, extraction pipeline (`MVP_NORTH_STAR.md`)

### Deferred

- Admin grant/revoke routes + UI → Phase 5 (`docs/ADMIN_V1_SPEC.md`); use Supabase for entitlements today
- Voice, advanced RAG, full DB course catalog (optional)

### Protected chat sequence (today)

1. Validate JWT → `user_id`
2. Fair-use quota check
3. Entitlement check when `course_slug` is set
4. Build context (schedule day, RAG, **short history** — not structured memory yet)
5. Generate reply

