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
- `DELETE /memory/{session_id}`

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

For persistent chat, add a direct Postgres URL in `.env`:

`SUPABASE_DB_URL=postgresql://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres?sslmode=require`
