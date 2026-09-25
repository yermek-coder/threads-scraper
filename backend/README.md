# FastAPI backend

The backend is the application API for the Threads monitoring dashboard. It uses async SQLAlchemy with PostgreSQL, stores its initial schema on startup, and creates the first dashboard user from `ADMIN_USERNAME` and `ADMIN_PASSWORD` when the users table is empty.

Run locally from the repository root after filling in the root `.env` file:

```sh
export CORS_ORIGINS=http://localhost:5173,http://localhost:3000
PYTHONPATH=backend uvicorn app.main:app --reload
```

The Docker Compose service exposes the API internally on port `8000`. Public API routes are under `/api`; `/health` is public for container health checks. API routes except webhook verification/event delivery require `Authorization: Bearer <token>`.

The initial database schema is created with SQLAlchemy `create_all`. For future schema changes, add Alembic migrations before rolling them out to an existing deployment.
