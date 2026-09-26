"""Keyword polling and persistence for discovered Threads posts."""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionFactory
from app.models import Keyword, Post
from app.services.filter_service import apply_post_filters, load_filter_context
from app.services.notifier import send_posts
from app.services.settings_service import get_setting
from app.services.threads_client import search_keyword
from app.services.threads_errors import ThreadsCollectionError
from app.services.threads_web_client import search_keyword_web

logger = logging.getLogger(__name__)


def parse_timestamp(value: Any) -> datetime | None:
    """Parse ISO timestamps returned by Threads and normalize them to UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def post_from_api(item: dict[str, Any], keyword_id: int | None) -> Post | None:
    """Map a Threads media object into the persistent post model."""
    post_id = item.get("id")
    if post_id is None:
        return None
    text = item.get("text")
    return Post(
        threads_post_id=str(post_id),
        text=text if isinstance(text, str) else "",
        author_username=str(item.get("username") or item.get("author_username") or ""),
        permalink=str(item.get("permalink") or ""),
        post_timestamp=parse_timestamp(item.get("timestamp")),
        likes_count=_nonnegative_int(item.get("like_count", item.get("likes_count", 0))),
        replies_count=_nonnegative_int(item.get("reply_count", item.get("replies_count", 0))),
        matched_keyword_id=keyword_id,
        raw_data=item,
    )


def _nonnegative_int(value: Any) -> int:
    """Convert API metrics to a safe nonnegative integer."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


async def collect_keyword(
    db: AsyncSession,
    keyword: Keyword,
    notify: bool = True,
    notification_batch: list[int] | None = None,
) -> dict[str, int]:
    """Fetch, filter, de-duplicate, and persist one keyword's latest results."""
    method = await get_setting(db, "threads_collection_method") or "web"
    if method == "api":
        records = await search_keyword(db, keyword.phrase)
    elif method == "web":
        records = await search_keyword_web(db, keyword.phrase)
    else:
        raise ThreadsCollectionError("Unknown Threads collection method")
    saved_posts: list[Post] = []
    filter_context = await load_filter_context(db) if records else None
    for item in records:
        post = post_from_api(item, keyword.id)
        if post is None:
            continue
        existing = await db.scalar(select(Post.id).where(Post.threads_post_id == post.threads_post_id))
        if existing is not None:
            continue
        try:
            async with db.begin_nested():
                db.add(post)
                await db.flush()
                assert filter_context is not None
                await apply_post_filters(post, filter_context)
                await db.flush()
        except IntegrityError:
            continue
        saved_posts.append(post)

    keyword.last_polled_at = datetime.now(timezone.utc)
    await db.commit()
    deliverable = [post for post in saved_posts if post.is_relevant and not post.is_duplicate]
    if notify and deliverable:
        await send_posts(db, deliverable)
    elif notification_batch is not None:
        notification_batch.extend(post.id for post in deliverable)
    return {"scanned": len(records), "saved": len(saved_posts)}


async def collect_keyword_id(keyword_id: int, notify: bool = True) -> dict[str, int]:
    """Load and collect one keyword in an isolated session."""
    async with SessionFactory() as db:
        keyword = await db.get(Keyword, keyword_id)
        if keyword is None:
            raise LookupError("Keyword not found")
        return await collect_keyword(db, keyword, notify=notify)


async def poll_active_keywords() -> None:
    """Scheduled job that polls each currently active keyword independently."""
    async with SessionFactory() as db:
        collection_method = await get_setting(db, "threads_collection_method") or "web"
        if collection_method == "api" and not await get_setting(db, "threads_access_token"):
            logger.info("Scheduled Threads collection skipped: access token is not configured")
            return
        if collection_method == "web" and not isinstance(await get_setting(db, "threads_web_storage_state"), dict):
            logger.info("Scheduled Threads collection skipped: web session is not configured")
            return
        keyword_ids = list((await db.scalars(select(Keyword.id).where(Keyword.is_active.is_(True)))).all())
        notification_batch: list[int] = []
        for keyword_id in keyword_ids:
            try:
                keyword = await db.get(Keyword, keyword_id)
                if keyword is None or not keyword.is_active:
                    continue
                await collect_keyword(db, keyword, notify=False, notification_batch=notification_batch)
            except Exception:
                await db.rollback()
                logger.exception("Scheduled Threads collection failed for keyword id %s", keyword_id)
        if notification_batch:
            posts = list((await db.scalars(select(Post).where(Post.id.in_(notification_batch)))).all())
            await send_posts(db, posts)
