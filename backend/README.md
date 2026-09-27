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

Collection uses the Threads search page only to discover canonical post permalinks. It does not read post text from search cards. The observed `useBarcelonaBatchedDynamicPostCountsSubscriptionQuery` response had IDs and counts but no body text, and the page's inline scheduler JSON did not provide a verified stable object with ID, author, and full content. The collector opens each discovered permalink, verifies the resulting route, matching permalink anchor, and author link, then parses the post card on that dedicated page.

The earlier ten-ancestor/first-`span[dir=auto]` selector is not used. Live inspection showed that its first span could be the author name (for example, on result `DdwD0qeCCug`) and its ancestor could contain neighboring results. This is why extraction from a dedicated post page is the primary path. If the page route, ID, author, and body structure do not agree, extraction is reported as incomplete, unavailable, or unknown rather than storing arbitrary page text. A reply whose own text cannot be separated from reply context is also rejected explicitly.

Every stored post has extraction metadata in `raw_data.extraction`: post ID, source, parser version, status, reason, and attempt time. Statuses are `success`, `no_text`, `incomplete`, `unavailable`, `unknown_structure`, and `legacy_unverified`. Legacy text is hidden from API responses until a successful re-extraction. Failed retries retain a previous confirmed result and are recorded separately as `extraction_last_attempt`. Only new confirmed text posts can trigger notifications.

Search pagination retains discovered IDs and permalinks across virtualized DOM batches and scrolls until the rendered identity set stabilizes, with bounded limits. An explicit end-of-feed marker has not been confirmed in the inspected states, so search coverage is reported as incomplete even when scrolling stabilizes. Each candidate is fetched separately and must pass its own permalink and author checks. In inspected reply pages, the reply-context label and own text were separate but not semantically labeled; when that structure cannot be distinguished safely, the parser returns `unknown_structure` instead of storing both as post text.

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
