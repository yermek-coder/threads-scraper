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

Collection now captures each bounded post card directly on the Threads search page while scrolling. Threads virtualizes the feed, so cards can disappear from the DOM as soon as they leave the viewport. Each captured card must have a canonical permalink, matching author profile link, and a separable body region before its text is accepted. The earlier ten-ancestor/first-`span[dir=auto]` selector is not used: it could select an author name or a neighboring result. A card with a "See more" control is marked incomplete because its visible text may be truncated.

The observed `useBarcelonaBatchedDynamicPostCountsSubscriptionQuery` response had IDs and counts but no body text, and the page's inline scheduler JSON did not provide a verified stable object with ID, author, and full content. Search-card HTML is parsed using the same identity and nested-content checks as direct post pages. If ID, author, header, and body structure do not agree, extraction is reported as incomplete or unknown rather than storing arbitrary page text. A reply whose own text cannot be separated from reply context is rejected explicitly. Direct permalink visits remain available for the separate legacy recovery command.

Every stored post has extraction metadata in `raw_data.extraction`: post ID, source, parser version, status, reason, and attempt time. Statuses are `success`, `no_text`, `incomplete`, `unavailable`, `unknown_structure`, and `legacy_unverified`. Legacy text is hidden from API responses until a successful re-extraction. Failed retries retain a previous confirmed result and are recorded separately as `extraction_last_attempt`. Only new confirmed text posts can trigger notifications.

Search pagination retains captured cards, IDs, permalinks, and visible engagement counts across virtualized DOM batches. It scrolls in bounded steps until the rendered set stabilizes or reaches a limit. An explicit end-of-feed marker has not been confirmed in the inspected states, so search coverage is reported as incomplete even when scrolling stabilizes. The dashboard can sort stored posts by likes + replies + reposts observed on search cards; missing values do not overwrite previously stored counts. In inspected reply pages, the reply-context label and own text were separate but not semantically labeled; when that structure cannot be distinguished safely, the parser returns `unknown_structure` instead of storing both as post text.

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

### Legacy text recovery

Run the recovery command without `--apply` first. This is a read-only dry run that opens up to 50 eligible legacy permalinks and prints IDs, extraction statuses, and reasons without printing post text or changing records:

```sh
PYTHONPATH=backend python backend/scripts/recover_post_text.py --limit 50
```

After reviewing the dry-run IDs and `plan_id`, apply one bounded batch by passing that exact plan fingerprint. If the candidate identities or permalinks changed since the dry run, the fingerprint will differ and the run will be refused. Successful rows are updated in place, stop-word relevance and duplicate relationships are recomputed, and no notifications are sent:

```sh
PYTHONPATH=backend python backend/scripts/recover_post_text.py --limit 50 --apply --confirm-plan "PASTE_PLAN_ID"
```

The collector automatically retries legacy or failed rows when their IDs reappear in search. Rows whose stored permalink does not match the stored post ID and author are excluded from bulk recovery for review.

Run the saved structural fixtures and regression cases with:

```sh
PYTHONPATH=backend python -m unittest discover -s backend/tests -v
```

## Jev relevance filter

The final relevance check can use Jev through OpenRouter. Save an OpenRouter API key in **Settings → Threads → Фильтр релевантности Jev** (or set `OPENROUTER_API_KEY` in the backend environment), enable the filter, and edit its instruction there. Jev checks each non-duplicate post against the keyword that found it. If OpenRouter is unavailable, collection keeps the existing deterministic relevance result.

Collection and relevance events are written to the backend container log as one-line JSON payloads after a stable event name. `run_id` correlates a keyword collection with its discovery, extraction, stop-word, duplicate, and Jev events. Jev records the model, prompt fingerprint, probability, threshold, final decision, latency, OpenRouter request/response IDs, usage, HTTP failures, and the fallback decision. Post text and API keys are not logged.

Follow production backend logs from the repository directory with:

```sh
docker compose -p threads-scraper -f docker-compose.yml -f deploy/oracle/docker-compose.yml logs -f backend
```

Filter to collection and relevance diagnostics with:

```sh
docker compose -p threads-scraper -f docker-compose.yml -f deploy/oracle/docker-compose.yml logs -f backend 2>&1 | grep -E 'threads\.collection_|threads\.discovery_|threads\.extraction_|jev\.|relevance\.'
```
