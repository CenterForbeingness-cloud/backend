# Sentient Backend (Bare Bones)

Minimal FastAPI backend for chat MVP.

## Current Backend Layout

- `app/main.py` API routes
- `app/models.py` request/response schema
- `app/ai.py` OpenAI/Claude routing and safe fallback replies
- `app/storage.py` storage abstraction (in-memory or Postgres)
- `app/config.py` app/environment configuration

## Architecture Note

Backend is deployed as a separate service from the Flutter app.
Flutter (Dart) connects to this backend over HTTP/WebSocket APIs.
Keep frontend and backend release pipelines independent.

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

The backend course catalog now prefers the Supabase course tables when `SUPABASE_DB_URL` is configured.
If that database connection is unavailable, it falls back to the local `rag/raw/courses/` directory so development still works.

The pricing flow is course-first:

- `GET /courses` returns published course metadata plus pricing fields when present in the database.
- `GET /entitlements` returns the authenticated user's owned course slugs.
- `POST /billing/payment-intent` and `POST /billing/checkout` both accept a `course_slug` so the backend can attach purchase metadata.
- The frontend pricing screen now renders from the course catalog instead of hardcoded cards.

For Supabase deployments, the relevant schema is documented in `backend/sql/supabase_courses_billing_rls.sql`.

For persistent chat, add a direct Postgres URL in `.env`:

`SUPABASE_DB_URL=postgresql://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres?sslmode=require`

SQL setup files:

- `backend/sql/supabase_chat_rls.sql` for chat session/message schema and RLS
- `backend/sql/supabase_courses_billing_rls.sql` for course catalog, Stripe purchase ownership, entitlements, and billing event tables

Stripe env variables expected by backend config:

- `STRIPE_SECRET_KEY` (server-side Stripe secret key)
- `STRIPE_PUBLISHABLE_KEY` (public key, returned to clients by future billing endpoints)
- `STRIPE_WEBHOOK_SECRET` (webhook signature verification)

Backward compatibility:

- `STRIPE_API_KEY` is still supported as a fallback alias for `STRIPE_SECRET_KEY`.

Stripe CLI and webhook testing guide:

- `backend/STRIPE_PAYMENTS.md`

## Next Backend Flow (Courses and Payments)

The next backend milestone is a course-aware, purchase-ownership, and entitlement-gated request path.

Required request sequence for protected chat:

1. Validate Supabase JWT and resolve `user_id`.
2. Validate request payload (`session_id`, `message`, optional `course_slug`, `week`).
3. Apply fair-use limits for abuse prevention (internal guardrail).
4. Enforce course ownership entitlement when `course_slug` is present.
5. Build context (base script, base transcript, selected course/week, retrieved chunks).
6. Generate AI response and persist message and usage events.

Recommended new backend modules:

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

- `POST /chat` accepts optional `course_slug` and `week`.
- Backend checks entitlement before course retrieval.

