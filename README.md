# Sentient Backend (Bare Bones)

Minimal FastAPI backend for chat MVP.

## Architecture Note

Backend is deployed as a separate service from the Flutter app.
Flutter (Dart) connects to this backend over HTTP/WebSocket APIs.
Keep frontend and backend release pipelines independent.

## Endpoints

- `GET /health`
- `POST /chat`
- `DELETE /memory/{session_id}`

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies:
   `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and add keys if needed.
4. Run:
   `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

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
   `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

## Verify It Is Running

- Root: `http://127.0.0.1:8000/`
- Health: `http://127.0.0.1:8000/health`
- Docs: `http://127.0.0.1:8000/docs`

## Chat Request Example

```json
{
  "session_id": "user-123",
  "message": "I feel stressed today",
  "provider": "openai"
}
```

If API keys are missing, backend returns a fallback echo so you can test app wiring first.
