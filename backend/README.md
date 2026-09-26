# FastAPI backend

The backend is the application API for the Threads monitoring dashboard. It uses async SQLAlchemy with PostgreSQL, stores its initial schema on startup, and creates the first dashboard user from `ADMIN_USERNAME` and `ADMIN_PASSWORD` when the users table is empty.

Run locally from the repository root after filling in the root `.env` file:

```sh
export CORS_ORIGINS=http://localhost:5173,http://localhost:3000
PYTHONPATH=backend uvicorn app.main:app --reload
```

The Docker Compose service exposes the API internally on port `8000`. Public API routes are under `/api`; `/health` is public for container health checks. API routes except webhook verification/event delivery require `Authorization: Bearer <token>`.

The initial database schema is created with SQLAlchemy `create_all`. For future schema changes, add Alembic migrations before rolling them out to an existing deployment.

## Threads collection

Collection uses the Threads web search page by default. It opens a headless Chromium session for each active keyword, visits the recent-results page, scrolls through the rendered post cards, and reads their text, author, permalink, and timestamp from the DOM. The web session is stored in the application settings database and is separate from the Meta API token.

To create or refresh the web session from a computer with a desktop browser:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'playwright>=1.50,<2.0'
python -m playwright install chromium
python backend/scripts/export_threads_session.py
```

The script opens Threads for manual sign-in and writes `~/threads-storage-state.json`. Import that file from **Settings → Threads → Авторизованная web-сессия**. The importer also accepts the cookie-list JSON export used by the older `abu-makhmed` scraper. Treat either file as a login secret. Re-import it after Threads expires the session.

The collection method can be changed in the same settings screen. `Threads web scraping` is the default; `Graph API · threads_keyword_search` remains available as an alternative and requires the Meta API token and permission.

## Jev relevance filter

The final relevance check can use Jev through OpenRouter. Save an OpenRouter API key in **Settings → Threads → Фильтр релевантности Jev** (or set `OPENROUTER_API_KEY` in the backend environment), enable the filter, and edit its instruction there. Jev checks each non-duplicate post against the keyword that found it. If OpenRouter is unavailable, collection keeps the existing deterministic relevance result.
