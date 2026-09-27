"""Threads webhook verification support and mention/reply event ingestion."""

import logging
import time
import uuid
from dataclasses import replace
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionFactory
from app.core.observability import log_event
from app.models import Post
from app.services.collector import parse_timestamp
from app.services.filter_service import apply_post_filters, load_filter_context
from app.services.notifier import send_posts
from app.services.threads_post_parser import ExtractionStatus, parse_api_post

logger = logging.getLogger(__name__)


def _event_posts(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield post-like objects nested below a mention/reply webhook field."""
    seen: set[str] = set()

    def walk(node: Any, relevant: bool) -> Iterator[dict[str, Any]]:
        if isinstance(node, dict):
            field = str(node.get("field", "")).casefold()
            current_relevant = relevant or any(term in field for term in ("mention", "reply"))
            if current_relevant and node.get("id") is not None and isinstance(node.get("text"), str):
                key_id = str(node["id"])
                if key_id not in seen:
                    seen.add(key_id)
                    yield node
                return
            for key, value in node.items():
                child_relevant = current_relevant or any(term in key.casefold() for term in ("mention", "reply"))
                yield from walk(value, child_relevant)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item, relevant)

    yield from walk(payload, False)


async def process_webhook_payload(
    db: AsyncSession,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> int:
    """Persist webhook mention/reply posts and send immediate alerts for new matches."""
    run_id = run_id or uuid.uuid4().hex
    started = time.perf_counter()
    incoming: list[Post] = []
    event_posts = list(_event_posts(payload))
    invalid_posts = 0
    already_present = 0
    log_event(
        logger,
        logging.INFO,
        "threads.webhook_started",
        run_id=run_id,
        event_posts=len(event_posts),
    )
    filter_context = await load_filter_context(db) if event_posts else None
    for item in event_posts:
        extraction = parse_api_post(item)
        if not extraction.post_id:
            invalid_posts += 1
            log_event(
                logger,
                logging.WARNING,
                "threads.extraction_failed",
                run_id=run_id,
                source=extraction.source,
                parser_version=extraction.parser_version,
                status=extraction.status.value,
                reason=extraction.reason,
                post_id=None,
            )
            continue
        extraction = replace(extraction, source="threads_webhook", parser_version="threads-webhook-v1")
        post_id = extraction.post_id
        exists = await db.scalar(select(Post.id).where(Post.threads_post_id == post_id))
        if exists is not None:
            already_present += 1
            log_event(
                logger,
                logging.INFO,
                "threads.webhook_post_skipped",
                run_id=run_id,
                post_id=post_id,
                reason="already_present",
            )
            continue
        if extraction.status != ExtractionStatus.SUCCESS:
            log_event(
                logger,
                logging.INFO,
                "threads.webhook_post_filtered",
                run_id=run_id,
                post_id=post_id,
                source=extraction.source,
                parser_version=extraction.parser_version,
                extraction_status=extraction.status.value,
                extraction_reason=extraction.reason,
                reason="extraction_not_success",
            )
        post = Post(
            threads_post_id=post_id,
            text=extraction.text or "" if extraction.confirmed else "",
            author_username=extraction.username,
            permalink=extraction.permalink,
            post_timestamp=parse_timestamp(extraction.timestamp),
            likes_count=extraction.likes_count,
            replies_count=extraction.replies_count,
            raw_data={"source": extraction.source, "extraction": extraction.diagnostic()},
        )
        try:
            async with db.begin_nested():
                db.add(post)
                await db.flush()
                if extraction.status == ExtractionStatus.SUCCESS:
                    assert filter_context is not None
                    await apply_post_filters(
                        post,
                        filter_context,
                        run_id=run_id,
                        source="threads_webhook",
                    )
                await db.flush()
        except IntegrityError:
            already_present += 1
            log_event(
                logger,
                logging.INFO,
                "threads.webhook_post_skipped",
                run_id=run_id,
                post_id=post_id,
                reason="concurrent_insert",
            )
            continue
        incoming.append(post)
    await db.commit()
    alerts = [
        post for post in incoming
        if post.raw_data.get("extraction", {}).get("status") == ExtractionStatus.SUCCESS.value
        and post.is_relevant
        and not post.is_duplicate
    ]
    if alerts:
        await send_posts(db, alerts, immediate=True)
    log_event(
        logger,
        logging.INFO,
        "threads.webhook_finished",
        run_id=run_id,
        event_posts=len(event_posts),
        persisted=len(incoming),
        invalid_posts=invalid_posts,
        already_present=already_present,
        alerts=len(alerts),
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    return len(incoming)


async def process_webhook_in_background(payload: dict[str, Any]) -> None:
    """Ingest and notify about a verified event after the HTTP response is sent."""
    run_id = uuid.uuid4().hex
    try:
        async with SessionFactory() as db:
            await process_webhook_payload(db, payload, run_id=run_id)
    except Exception as error:
        log_event(
            logger,
            logging.ERROR,
            "threads.webhook_failed",
            run_id=run_id,
            exception_type=type(error).__name__,
            exc_info=not isinstance(error, SQLAlchemyError),
        )
