"""Keyword polling and persistence for discovered Threads posts."""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionFactory
from app.models import Keyword, Post
from app.services.filter_service import recalculate_post_filter_state
from app.services.notifier import send_posts
from app.services.settings_service import get_setting
from app.services.threads_client import search_keyword
from app.services.threads_discovery import DiscoveredPost
from app.services.threads_errors import ThreadsCollectionError
from app.services.threads_post_parser import (
    CONFIRMED_STATUSES,
    ExtractionStatus,
    PostExtraction,
    SEARCH_DOM_SOURCE,
    SEARCH_PARSER_VERSION,
    add_extraction_diagnostic,
    extraction_is_currently_confirmed,
    extraction_diagnostic,
    parse_api_post,
    sanitized_search_engagement,
)
from app.services.threads_web_client import discover_keyword_posts

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
    """Map an API object after its post identity and text fields were validated."""
    extraction = parse_api_post(item)
    if not extraction.post_id:
        return None
    return Post(
        threads_post_id=extraction.post_id,
        text=extraction.text if extraction.confirmed and extraction.text is not None else "",
        author_username=extraction.username,
        permalink=extraction.permalink,
        post_timestamp=parse_timestamp(extraction.timestamp),
        likes_count=extraction.likes_count,
        replies_count=extraction.replies_count,
        matched_keyword_id=keyword_id,
        raw_data={"source": extraction.source, "extraction": extraction.diagnostic()},
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
) -> dict[str, int | bool | str | None]:
    """Fetch, filter, de-duplicate, and persist one keyword's latest results."""
    method = await get_setting(db, "threads_collection_method") or "web"
    if method == "api":
        api_records = await search_keyword(db, keyword.phrase)
        extractions = [parse_api_post(item) for item in api_records]
        scanned = len(api_records)
        discovery_complete = True
        discovery_reason = None
    elif method == "web":
        discovery = await discover_keyword_posts(db, keyword.phrase)
        scanned = len(discovery.posts)
        discovery_complete = discovery.complete
        discovery_reason = discovery.reason
        if not discovery.complete:
            logger.warning(
                "Threads discovery diagnostic source=threads_search_dom parser_version=%s status=%s reason=%s rounds=%s",
                SEARCH_PARSER_VERSION,
                ExtractionStatus.INCOMPLETE.value,
                discovery.reason,
                discovery.rounds,
            )
        existing_rows = await _load_existing_posts(db, [candidate.post_id for candidate in discovery.posts])
        candidates_to_extract = [
            candidate for candidate in discovery.posts
            if not _is_confirmed(existing_rows.get(candidate.post_id), candidate.post_id)
        ]
        extractions = [
            _search_extraction(candidate, discovery.extractions.get(candidate.post_id))
            for candidate in candidates_to_extract
        ]
        candidates_by_id = {candidate.post_id: candidate for candidate in discovery.posts}
        for candidate in discovery.posts:
            post = existing_rows.get(candidate.post_id)
            if post is not None:
                _apply_search_engagement(post, candidate)
    else:
        raise ThreadsCollectionError("Unknown Threads collection method")

    extraction_failures = 0
    recovered_rows = 0
    saved_posts: list[Post] = []
    affected_post_ids: set[int] = set()
    if method == "api":
        existing_rows = await _load_existing_posts(
            db,
            [extraction.post_id for extraction in extractions if extraction.post_id],
        )

    for extraction in extractions:
        if not extraction.post_id:
            extraction_failures += 1
            logger.error(
                "Threads extraction diagnostic post_id=* source=%s parser_version=%s status=%s reason=%s",
                extraction.source,
                extraction.parser_version,
                extraction.status.value,
                extraction.reason,
            )
            continue

        post = existing_rows.get(extraction.post_id)
        if post is not None:
            current = extraction_diagnostic(post.raw_data, post.threads_post_id)
            was_confirmed = current.get("status") in CONFIRMED_STATUSES
            was_currently_confirmed = extraction_is_currently_confirmed(
                post.raw_data,
                post.threads_post_id,
            )
            previous_text = post.text
            if extraction.confirmed:
                _apply_confirmed_extraction(post, extraction)
                content_changed = (
                    not was_currently_confirmed
                    or previous_text != (extraction.text or "")
                    or extraction.status.value != current.get("status")
                )
                if content_changed:
                    recovered_rows += 1
                if content_changed:
                    affected_post_ids.add(post.id)
            else:
                if was_confirmed:
                    # Preserve text and confirmed diagnostic; only retain the retry result.
                    post.raw_data = add_extraction_diagnostic(post.raw_data, extraction)
                else:
                    _apply_unconfirmed_extraction(post, extraction)
                extraction_failures += 1
            continue

        post = _new_post(extraction, keyword.id)
        if method == "web":
            _apply_search_engagement(post, candidates_by_id[extraction.post_id])
        try:
            async with db.begin_nested():
                db.add(post)
                await db.flush()
        except IntegrityError:
            # Another collector may have inserted this ID between discovery and save.
            continue
        existing_rows[extraction.post_id] = post
        saved_posts.append(post)
        if extraction.confirmed:
            affected_post_ids.add(post.id)
        elif not extraction.confirmed:
            extraction_failures += 1

    if affected_post_ids:
        await recalculate_post_filter_state(db, affected_post_ids)

    keyword.last_polled_at = datetime.now(timezone.utc)
    await db.commit()
    deliverable = [
        post for post in saved_posts
        if extraction_diagnostic(post.raw_data, post.threads_post_id).get("status") == ExtractionStatus.SUCCESS.value
        and post.is_relevant
        and not post.is_duplicate
    ]
    if notify and deliverable:
        await send_posts(db, deliverable)
    elif notification_batch is not None:
        notification_batch.extend(post.id for post in deliverable)
    return {
        "scanned": scanned,
        "saved": len(saved_posts),
        "updated": recovered_rows,
        "extraction_failures": extraction_failures,
        "discovery_complete": discovery_complete,
        "discovery_reason": discovery_reason,
    }


async def _load_existing_posts(db: AsyncSession, post_ids: list[str]) -> dict[str, Post]:
    if not post_ids:
        return {}
    posts = list((await db.scalars(select(Post).where(Post.threads_post_id.in_(set(post_ids))))).all())
    return {post.threads_post_id: post for post in posts}


def _is_confirmed(post: Post | None, expected_post_id: str) -> bool:
    if post is None:
        return False
    return (
        post.threads_post_id == expected_post_id
        and extraction_is_currently_confirmed(post.raw_data, expected_post_id)
    )


def _search_extraction(candidate: DiscoveredPost, extraction: PostExtraction | None) -> PostExtraction:
    """Reject a search result unless its extraction belongs to this exact card."""
    if extraction is not None and (
        extraction.post_id == candidate.post_id
        and extraction.username.casefold() == candidate.username.casefold()
        and extraction.permalink == candidate.permalink
        and extraction.source == SEARCH_DOM_SOURCE
        and extraction.parser_version == SEARCH_PARSER_VERSION
    ):
        return extraction
    return PostExtraction(
        post_id=candidate.post_id,
        username=candidate.username,
        permalink=candidate.permalink,
        text=None,
        status=ExtractionStatus.INCOMPLETE if extraction is None else ExtractionStatus.UNKNOWN_STRUCTURE,
        source=SEARCH_DOM_SOURCE,
        parser_version=SEARCH_PARSER_VERSION,
        reason="search_card_extraction_result_missing" if extraction is None else "search_card_extraction_identity_mismatch",
    )


def _new_post(extraction: PostExtraction, keyword_id: int) -> Post:
    return Post(
        threads_post_id=extraction.post_id,
        text=(extraction.text or "") if extraction.confirmed else "",
        author_username=extraction.username,
        permalink=extraction.permalink,
        post_timestamp=parse_timestamp(extraction.timestamp),
        likes_count=extraction.likes_count,
        replies_count=extraction.replies_count,
        matched_keyword_id=keyword_id,
        raw_data={"source": extraction.source, "extraction": extraction.diagnostic()},
    )


def _apply_search_engagement(post: Post, candidate: DiscoveredPost) -> None:
    """Update only counts observed on the search card, keeping prior known values."""
    if candidate.likes_count is not None:
        post.likes_count = candidate.likes_count
    if candidate.replies_count is not None:
        post.replies_count = candidate.replies_count

    raw_data = post.raw_data if isinstance(post.raw_data, dict) else {}
    previous = sanitized_search_engagement(raw_data)
    if previous is None and all(
        count is None for count in (candidate.likes_count, candidate.replies_count, candidate.reposts_count)
    ):
        return
    previous = previous or {"reposts_count": 0, "likes_count": None, "replies_count": None}
    raw_data = dict(raw_data)
    raw_data["search_engagement"] = {
        "reposts_count": candidate.reposts_count if candidate.reposts_count is not None else previous["reposts_count"],
        "likes_count": candidate.likes_count if candidate.likes_count is not None else previous["likes_count"],
        "replies_count": candidate.replies_count if candidate.replies_count is not None else previous["replies_count"],
    }
    post.raw_data = raw_data


def _apply_confirmed_extraction(post: Post, extraction: PostExtraction) -> None:
    post.text = extraction.text or ""
    post.author_username = extraction.username
    post.permalink = extraction.permalink
    parsed_timestamp = parse_timestamp(extraction.timestamp)
    if parsed_timestamp is not None:
        post.post_timestamp = parsed_timestamp
    if extraction.source == "threads_graph_api":
        post.likes_count = extraction.likes_count
        post.replies_count = extraction.replies_count
    if extraction.status == ExtractionStatus.NO_TEXT:
        post.is_relevant = True
        post.is_duplicate = False
        post.duplicate_of_id = None
    raw_data = add_extraction_diagnostic(post.raw_data, extraction)
    raw_data["source"] = extraction.source
    post.raw_data = raw_data


def _apply_unconfirmed_extraction(post: Post, extraction: PostExtraction) -> None:
    post.text = ""
    if extraction.username:
        post.author_username = extraction.username
    if extraction.permalink:
        post.permalink = extraction.permalink
    post.is_relevant = True
    post.is_duplicate = False
    post.duplicate_of_id = None
    raw_data = add_extraction_diagnostic(post.raw_data, extraction)
    raw_data["source"] = extraction.source
    post.raw_data = raw_data


async def collect_keyword_id(keyword_id: int, notify: bool = True) -> dict[str, int | bool | str | None]:
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
