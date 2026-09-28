"""Async client for Threads Graph API search and long-lived token refresh."""

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import config
from app.services.settings_service import get_setting, save_setting
from app.services.threads_errors import ThreadsCollectionError

_quota_lock = asyncio.Lock()


class ThreadsAPIError(ThreadsCollectionError):
    """Raised when Threads rejects a request or its response is invalid."""


async def _reserve_daily_call(db: AsyncSession) -> None:
    """Atomically reserve one API call against the configured daily budget."""
    async with _quota_lock:
        today = datetime.now(timezone.utc).date().isoformat()
        usage = await get_setting(db, "threads_daily_usage")
        if not isinstance(usage, dict) or usage.get("date") != today:
            usage = {"date": today, "count": 0}
        count = int(usage.get("count", 0))
        if count >= config.threads_daily_call_limit:
            raise ThreadsAPIError("Daily Threads API call limit reached")
        usage["count"] = count + 1
        await save_setting(db, "threads_daily_usage", usage)
        await db.commit()


def _base_url() -> str:
    """Return a normalized API base URL, retaining an optional version path."""
    return config.threads_api_base_url.rstrip("/")


async def search_keyword(
    db: AsyncSession, phrase: str, section: str = "recent"
) -> list[dict[str, Any]]:
    """Search recent public posts for a phrase using Threads keyword_search."""
    if section not in {"recent", "top"}:
        raise ValueError("Unknown Threads search section")
    token = await get_setting(db, "threads_access_token")
    if not isinstance(token, str) or not token:
        raise ThreadsAPIError("Threads access token is not configured")
    await _reserve_daily_call(db)
    params = {
        "q": phrase,
        "search_type": section.upper(),
        "search_mode": "KEYWORD",
        "fields": "id,text,username,permalink,timestamp,like_count,reply_count,has_replies,is_reply",
        "limit": "50",
        "access_token": token,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        try:
            response = await client.get(f"{_base_url()}/keyword_search", params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("error", {}).get("message", "request rejected")
            except (ValueError, AttributeError):
                detail = "request rejected"
            safe_detail = str(detail).replace(token, "[redacted]")[:500]
            raise ThreadsAPIError(f"Threads API error: {safe_detail}") from exc
        except httpx.HTTPError as exc:
            raise ThreadsAPIError("Threads API request failed due to a network error") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise ThreadsAPIError("Threads API returned invalid JSON") from exc
    data = payload.get("data", [])
    if not isinstance(data, list):
        raise ThreadsAPIError("Threads API response has no valid data list")
    return [item for item in data if isinstance(item, dict)]


async def refresh_access_token(db: AsyncSession) -> bool:
    """Refresh a long-lived Threads token and persist its replacement."""
    token = await get_setting(db, "threads_access_token")
    if not isinstance(token, str) or not token:
        return False
    await _reserve_daily_call(db)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        try:
            response = await client.get(
                f"{_base_url()}/refresh_access_token",
                params={"grant_type": "th_refresh_token", "access_token": token},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise ThreadsAPIError("Threads token refresh failed due to an API or network error") from None
    refreshed_token = payload.get("access_token")
    if not isinstance(refreshed_token, str) or not refreshed_token:
        raise ThreadsAPIError("Threads token refresh returned no access token")
    await save_setting(db, "threads_access_token", refreshed_token)
    await save_setting(db, "threads_last_token_refresh", datetime.now(timezone.utc).isoformat())
    await db.commit()
    return True
