# JobOps

A personal job-application tracker: one place for every application, the exact CV
submitted, and the full history of what happened.

Local, single-user, no authentication. Runs on your laptop.

## Stack

| Part | Choice |
|---|---|
| Backend | Python 3.11 + FastAPI + SQLAlchemy 2.0 |
| Database | PostgreSQL 16 (in Docker) |
| Migrations | Alembic |
| Frontend | React 18 + TypeScript + Vite |
| Files | Content-addressed local storage under `data/documents/` |

## Running it

Three things need to be running: PostgreSQL, the API, and the UI.

### 1. PostgreSQL

```bash
docker compose up -d
```

Data lives in a named Docker volume, so `docker compose down` keeps it.
`docker compose down -v` deletes it — that is the destructive one.

### 2. Backend API

```bash
cd backend && .venv/Scripts/activate && uvicorn app.main:app --reload --port 8000
```

On first run, or after pulling new migrations:

```bash
cd backend && .venv/Scripts/activate && alembic upgrade head
```

- API: <http://localhost:8000>
- Interactive API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health> and `/health/db`

### 3. Frontend

```bash
cd frontend && npm run dev
```

Open <http://localhost:5173>.

The port matters: the backend's `CORS_ORIGINS` allows `http://localhost:5173`,
so the browser will block requests from any other port.

## Configuration

| File | Purpose |
|---|---|
| `.env` (repo root) | Backend: `DATABASE_URL`, `TEST_DATABASE_URL`, `DOCUMENTS_ROOT`, `CORS_ORIGINS` |
| `frontend/.env` | Frontend: `VITE_API_BASE_URL` |

Both have a committed `.env.example`. The real `.env` files are gitignored.

## Tests

```bash
cd backend && .venv/Scripts/python.exe -m pytest
```

```bash
cd frontend && npm run test && npm run lint && npm run build
```

Backend tests run against a real `jobops_test` PostgreSQL database, built by
running the actual Alembic migrations. A guard in `tests/conftest.py` refuses to
run if that database is not named exactly `jobops_test`, so a misconfigured
`.env` can never wipe your development data.

## Project layout

```
jobops/
├── docker-compose.yml       PostgreSQL only; the app runs natively
├── docker/initdb/           creates jobops_test on first volume init
├── data/documents/          uploaded files (gitignored)
├── backend/
│   ├── alembic/versions/    migrations
│   └── app/
│       ├── api/             HTTP routers — no business logic
│       ├── services/        business rules live here
│       ├── models/          SQLAlchemy tables
│       ├── schemas/         Pydantic request/response shapes
│       ├── core/            storage, normalisation, errors
│       ├── config.py db.py enums.py main.py
└── frontend/
    └── src/
        ├── api/             the only place fetch() is called
        ├── components/      shared UI pieces
        ├── pages/           one per route
        ├── hooks/           useAsync, useDebounced
        └── lib/             formatting helpers
```

## Design notes

Two rules explain most of the backend:

1. **The event log is the truth; `applications.status` is a cached projection.**
   Exactly one function assigns that column, and it always writes a
   `status_changed` timeline event in the same transaction. Status therefore
   cannot change without the history explaining it — which is why `PATCH` rejects
   a `status` field and there is a dedicated `POST /applications/{id}/status`.

2. **Documents are named after the SHA-256 of their own contents.** The same file
   is never stored twice, an existing document can never be silently altered, and
   an uploaded filename is never used to build a filesystem path.

## Status

- Phase 0 — project skeleton, Docker, Alembic ✅
- Phase 1 — applications + timeline + status service ✅
- Phase 2 — document library + submitted CV ✅
- Phase 3 — React UI ✅
- Phase 3.5 — use it daily for two weeks before adding anything
- Later — Chrome extension, Gmail ingestion, Claude fallback, scheduled jobs
