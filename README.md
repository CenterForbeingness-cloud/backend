# Sentient Backend

FastAPI backend for the AI chat companion, adaptive memory, progress tracking, and support content.

## Current Backend Layout

- `app/main.py` API routes
- `app/models.py` request/response schema
- `app/ai.py` OpenAI/Claude routing and safe fallback replies
- `app/storage.py` storage abstraction (in-memory or Postgres)
- `app/daily_schedule.py` daily schedule parse, import, and chat context
- `app/config.py` app/environment configuration
- `scripts/import_daily_schedule.py` import `schedules/*.txt` into Postgres

## Architecture Note

Backend is deployed as a separate service from the Flutter app.
Flutter (Dart) connects to this backend over HTTP/WebSocket APIs.
Keep frontend and backend release pipelines independent.

Companion-first product direction:

- `/chat` is the main user-facing interface.
- Memory, goals, habits, preferences, and check-ins should be treated as first-class backend concepts.
- Courses remain supported as structured learning content, but they are secondary to the companion experience.
- Notifications, summaries, reminders, and recommendation logic should grow around the user profile rather than around a course catalog.

## Endpoints

- `GET /health`
- `POST /chat`
- `POST /sessions`
- `GET /sessions/{session_id}/messages?limit=50`
- `DELETE /memory/{session_id}`
- `POST /billing/webhook`
- `POST /billing/checkout`
- `POST /billing/payment-intent`
- `GET /courses`
- `GET /courses/{course_slug}`
- `GET /entitlements`
- `GET /usage`

`POST /sessions` accepts an optional `session_id` and returns the active session id.
`GET /sessions/{session_id}/messages` returns recent messages for that session.

## Storage

Set `SUPABASE_DB_URL` to your Supabase Postgres connection string to persist chat history.
If it is omitted, the backend falls back to in-memory session storage.

If `SUPABASE_DB_URL` is set but invalid/unreachable, startup falls back to in-memory storage.

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
- `backend/sql/supabase_courses_billing_rls.sql` for course catalog, Stripe purchase ownership, entitlements, and billing event tables
- `backend/sql/supabase_course_daily_schedule.sql` for day-by-day course schedule rows (imported from external text files)

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
2. Set `SUPABASE_DB_URL` in `backend/.env`.
3. Insert a catalog row, e.g. `mindful-foundations` (SQL in `schedules/README.md`).
4. Import from `backend/`:

   ```bash
   python scripts/import_daily_schedule.py --course-slug mindful-foundations --file ../schedules/mindful-foundations.example.txt
   ```

5. Confirm rows: `SELECT * FROM public.course_daily_schedule WHERE course_slug = 'mindful-foundations' ORDER BY day_number;`

### Chat behavior

`POST /chat` accepts optional `day_number` (with `course_slug`). When set, the backend loads that day from the database and prepends it to retrieved context before the AI call. Pinecone remains optional for broader course questions.

Example body:

```json
{
  "session_id": "session-abc",
  "message": "What should I focus on today?",
  "course_slug": "mindful-foundations",
  "day_number": 1
}
```

Requires course entitlement when `AUTH_ENFORCED=true` and `course_slug` is set. User progress (server-side “current day”) is not implemented yet.

### Code

- `app/daily_schedule.py` — parse, `replace_schedule`, `get_schedule_day`
- `scripts/import_daily_schedule.py` — CLI import (replaces all days for a slug)

Stripe env variables expected by backend config:

- `STRIPE_SECRET_KEY` (server-side Stripe secret key)
- `STRIPE_PUBLISHABLE_KEY` (public key, returned to clients by future billing endpoints)
- `STRIPE_WEBHOOK_SECRET` (webhook signature verification)

Backward compatibility:

- `STRIPE_API_KEY` is still supported as a fallback alias for `STRIPE_SECRET_KEY`.

Stripe CLI and webhook testing guide:

- `backend/STRIPE_PAYMENTS.md`

## Next Backend Flow (Courses and Payments)

The next backend milestone is a companion-aware request path that still supports course purchases and entitlement gating.

Required request sequence for protected chat:

1. Validate Supabase JWT and resolve `user_id`.
2. Validate request payload (`session_id`, `message`, optional `course_slug`, `week`).
3. Apply fair-use limits for abuse prevention (internal guardrail).
4. Load companion memory and profile context.
5. Enforce course ownership entitlement when `course_slug` is present.
6. Build context in priority order: base script, daily schedule day (if `day_number`), selected course/week, retrieved chunks, and conversation history.
7. Generate AI response and persist message, usage, and progress events.

Recommended new backend modules:

- `app/memory.py` companion profile, goals, habits, and check-in history
- `app/checkins.py` daily and scheduled check-in orchestration
- `app/recommendations.py` adaptive suggestions and next-step planning
- `app/courses.py` course catalog and lesson service
- `app/entitlements.py` entitlement checks and gating rules
- `app/billing.py` checkout and webhook reconciliation
- `app/quotas.py` request budgeting and usage counters

Minimum additional endpoints:

- `GET /courses`
- `GET /courses/{course_slug}`
- `GET /billing/products`
- `GET /billing/purchases`
- `POST /billing/checkout` (course purchase)
- `POST /billing/webhook`
- `GET /entitlements`
- `GET /usage`

Chat contract extension target:

- `POST /chat` accepts optional `course_slug`, `week_number`, and `day_number`.
- Backend checks entitlement before course retrieval.

