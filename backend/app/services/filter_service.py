"""Stop-word filtering and exact/fuzzy duplicate detection."""

import asyncio
import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionFactory
from app.core.observability import log_event
from app.models import Keyword, Post, StopWord
from app.services.settings_service import DEFAULT_JEV_RELEVANCE_PROMPT, get_setting
from app.services.threads_post_parser import CONFIRMED_STATUSES, extraction_diagnostic

FUZZY_DUPLICATE_THRESHOLD = 85
DUPLICATE_CANDIDATE_LIMIT = 2000
JEV_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"
JEV_RELEVANCE_THRESHOLD = 0.5
JEV_REPROCESS_DAYS = 7
JEV_REPROCESS_CONCURRENCY = 2

logger = logging.getLogger(__name__)


@dataclass
class FilterContext:
    """Stop words and lightweight canonical posts cached for one ingestion batch."""

    stop_words: tuple[str, ...]
    duplicate_candidates: list[tuple[int, str]] = field(default_factory=list)
    openrouter_api_key: str | None = None
    jev_relevance_enabled: bool = False
    jev_relevance_prompt: str = DEFAULT_JEV_RELEVANCE_PROMPT


async def load_filter_context(db: AsyncSession) -> FilterContext:
    """Load stop words once and only id/text for the latest canonical posts."""
    stop_words = tuple((await db.scalars(select(StopWord.word_or_phrase))).all())
    rows = (
        await db.execute(
            select(Post.id, Post.threads_post_id, Post.text, Post.raw_data)
            .where(Post.is_duplicate.is_(False))
            .order_by(Post.created_at.desc())
            .limit(DUPLICATE_CANDIDATE_LIMIT)
        )
    ).all()
    candidates = [
        (post_id, text)
        for post_id, threads_post_id, text, raw_data in rows
        if text and extraction_diagnostic(raw_data, threads_post_id).get("status") in CONFIRMED_STATUSES
    ]
    api_key = await get_setting(db, "openrouter_api_key")
    return FilterContext(
        stop_words=stop_words,
        duplicate_candidates=candidates,
        openrouter_api_key=api_key if isinstance(api_key, str) and api_key else None,
        jev_relevance_enabled=bool(await get_setting(db, "jev_relevance_enabled")),
        jev_relevance_prompt=await get_setting(db, "jev_relevance_prompt") or DEFAULT_JEV_RELEVANCE_PROMPT,
    )


def contains_stop_word(text: str, word_or_phrase: str) -> bool:
    """Match phrases as substrings and single words on token boundaries."""
    haystack = text.casefold()
    needle = word_or_phrase.casefold().strip()
    if not needle:
        return False
    if any(character.isspace() for character in needle):
        return needle in haystack
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack, flags=re.UNICODE) is not None


def _matched_stop_word(text: str, stop_words: tuple[str, ...]) -> str | None:
    """Return the first configured stop word found in text, if any."""
    return next((word for word in stop_words if contains_stop_word(text, word)), None)


def _find_duplicate_id(normalized_text: str, candidates: list[tuple[int, str]]) -> int | None:
    """Find a matching canonical post; called in a worker thread for CPU work."""
    for candidate_id, candidate_text in candidates:
        normalized_candidate = " ".join(candidate_text.casefold().split())
        if not normalized_candidate:
            continue
        if normalized_text == normalized_candidate:
            return candidate_id
        if fuzz.token_sort_ratio(normalized_text, normalized_candidate) > FUZZY_DUPLICATE_THRESHOLD:
            return candidate_id
    return None


async def apply_post_filters(
    post: Post,
    context: FilterContext,
    keyword: str = "",
    *,
    run_id: str | None = None,
    source: str = "webhook",
) -> Post:
    """Set stop-word, duplicate, and optional Jev relevance state for one post."""
    log_fields = {
        "run_id": run_id,
        "source": source,
        "post_pk": post.id,
        "post_id": post.threads_post_id,
        "keyword_id": post.matched_keyword_id,
        "keyword": keyword,
    }
    if extraction_diagnostic(post.raw_data, post.threads_post_id).get("status") not in CONFIRMED_STATUSES:
        post.is_relevant = True
        post.is_duplicate = False
        post.duplicate_of_id = None
        log_event(
            logger,
            logging.INFO,
            "relevance.filter_skipped",
            **log_fields,
            reason="extraction_unconfirmed",
            final_relevant=post.is_relevant,
        )
        return post
    matched_stop_word = _matched_stop_word(post.text, context.stop_words)
    post.is_relevant = matched_stop_word is None
    normalized = " ".join(post.text.casefold().split())
    duplicate_id: int | None = None
    if normalized:
        duplicate_id = await asyncio.to_thread(_find_duplicate_id, normalized, context.duplicate_candidates)
        if duplicate_id is not None:
            post.is_duplicate = True
            post.duplicate_of_id = duplicate_id
    if matched_stop_word is not None:
        log_event(
            logger,
            logging.INFO,
            "relevance.filter_applied",
            **log_fields,
            stage="stop_words",
            matched_stop_word=matched_stop_word,
            decision=False,
        )
    if duplicate_id is not None:
        log_event(
            logger,
            logging.INFO,
            "post.duplicate_detected",
            **log_fields,
            duplicate_of_id=duplicate_id,
        )
    if (
        post.is_relevant
        and not post.is_duplicate
        and context.jev_relevance_enabled
        and context.openrouter_api_key
    ):
        decision = await _jev_relevance_decision(
            post.text,
            keyword,
            context,
            post_pk=post.id,
            post_id=post.threads_post_id,
            keyword_id=post.matched_keyword_id,
            run_id=run_id,
            source=source,
            fallback_is_relevant=post.is_relevant,
        )
        if decision is not None:
            post.is_relevant = decision
        log_event(
            logger,
            logging.INFO,
            "relevance.filter_final",
            **log_fields,
            stage="jev" if decision is not None else "jev_fallback",
            decision=post.is_relevant,
        )
    else:
        reason = (
            "stop_word"
            if matched_stop_word is not None
            else "duplicate"
            if post.is_duplicate
            else "jev_disabled"
            if not context.jev_relevance_enabled
            else "api_key_missing"
        )
        log_event(
            logger,
            logging.INFO,
            "relevance.filter_skipped",
            **log_fields,
            reason=reason,
            final_relevant=post.is_relevant,
        )
    return post


def remember_post(post: Post, context: FilterContext) -> None:
    """Add a successfully saved canonical post to the current batch's duplicate candidates."""
    if (
        extraction_diagnostic(post.raw_data, post.threads_post_id).get("status") in CONFIRMED_STATUSES
        and not post.is_duplicate
        and " ".join(post.text.casefold().split())
    ):
        context.duplicate_candidates.append((post.id, post.text))


async def _jev_relevance_decision(
    post_text: str,
    keyword: str,
    context: FilterContext,
    *,
    post_pk: int | None = None,
    post_id: str | None = None,
    keyword_id: int | None = None,
    run_id: str | None = None,
    source: str = "unknown",
    fallback_is_relevant: bool = True,
) -> bool | None:
    """Ask Jev whether a post is relevant; keep deterministic results if the API fails."""
    started = time.perf_counter()
    prompt_fingerprint = hashlib.sha256(context.jev_relevance_prompt.encode("utf-8")).hexdigest()[:12]
    log_fields = {
        "run_id": run_id,
        "source": source,
        "post_pk": post_pk,
        "post_id": post_id,
        "keyword_id": keyword_id,
        "keyword": keyword,
        "model": JEV_MODEL,
        "prompt_sha256": prompt_fingerprint,
    }
    log_event(
        logger,
        logging.INFO,
        "jev.request_started",
        **log_fields,
        threshold=JEV_RELEVANCE_THRESHOLD,
        timeout_ms=20_000,
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            response = await client.post(
                JEV_DECISIONS_URL,
                headers={
                    "Authorization": f"Bearer {context.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": JEV_MODEL,
                    "state": {"post": post_text, "keyword": keyword},
                    "questions": {
                        "relevant": {
                            "type": "noul",
                            "instructions": context.jev_relevance_prompt,
                        }
                    },
                },
            )
        if not response.is_success:
            duration_ms = round((time.perf_counter() - started) * 1000)
            request_id = response.headers.get("x-request-id")
            error_code = None
            try:
                error_payload = response.json()
                api_error = error_payload.get("error") if isinstance(error_payload, dict) else None
                error_code = api_error.get("code") if isinstance(api_error, dict) else None
            except ValueError:
                pass
            log_event(
                logger,
                logging.WARNING,
                "jev.request_failed",
                **log_fields,
                failure="http_status",
                http_status=response.status_code,
                error_code=error_code,
                request_id=request_id,
                duration_ms=duration_ms,
                fallback_relevant=fallback_is_relevant,
            )
            log_event(
                logger,
                logging.WARNING,
                "jev.fallback_applied",
                **log_fields,
                reason="http_status",
                final_relevant=fallback_is_relevant,
            )
            return None
        payload = response.json()
        answer = payload["answers"]["relevant"]
        probability = float(answer["noul"])
        if answer.get("type") != "noul" or not 0.0 <= probability <= 1.0:
            raise ValueError("Unexpected Jev relevance response")
        decision = probability >= JEV_RELEVANCE_THRESHOLD
        usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        log_event(
            logger,
            logging.INFO,
            "jev.decision_completed",
            **log_fields,
            probability=round(probability, 6),
            threshold=JEV_RELEVANCE_THRESHOLD,
            decision=decision,
            http_status=response.status_code,
            request_id=response.headers.get("x-request-id"),
            response_id=payload.get("id") if isinstance(payload, dict) else None,
            provider=payload.get("provider") if isinstance(payload, dict) else None,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cost=usage.get("cost"),
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return decision
    except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as error:
        failure = (
            "timeout"
            if isinstance(error, httpx.TimeoutException)
            else "transport_error"
            if isinstance(error, httpx.HTTPError)
            else "invalid_response"
        )
        log_event(
            logger,
            logging.WARNING,
            "jev.request_failed",
            **log_fields,
            failure=failure,
            exception_type=type(error).__name__,
            duration_ms=round((time.perf_counter() - started) * 1000),
            fallback_relevant=fallback_is_relevant,
        )
        log_event(
            logger,
            logging.WARNING,
            "jev.fallback_applied",
            **log_fields,
            reason=failure,
            final_relevant=fallback_is_relevant,
        )
        return None


def _duplicate_assignments(rows: list[tuple[int, str]]) -> dict[int, int | None]:
    """Rebuild the deterministic duplicate graph from oldest to newest rows."""
    canonical: list[tuple[int, str]] = []
    assignments: dict[int, int | None] = {}
    for post_id, text in rows:
        normalized = " ".join(text.casefold().split())
        if not normalized:
            assignments[post_id] = None
            continue
        duplicate_id = _find_duplicate_id(normalized, canonical)
        assignments[post_id] = duplicate_id
        if duplicate_id is None:
            canonical.append((post_id, text))
    return assignments


async def recalculate_post_filter_state(
    db: AsyncSession,
    affected_post_ids: set[int],
    *,
    run_id: str | None = None,
    source: str = "collection",
) -> None:
    """Recompute verified duplicate links and filters after corrected text is saved.

    The recent candidate window matches normal ingestion. Rows outside that
    window are included when they are directly affected by a changed canonical
    post. This function never sends notifications.
    """
    if not affected_post_ids:
        return
    run_id = run_id or uuid.uuid4().hex
    context = await load_filter_context(db)
    dependent_ids = set(
        (
            await db.scalars(
                select(Post.id).where(Post.duplicate_of_id.in_(affected_post_ids))
            )
        ).all()
    )
    included_ids = affected_post_ids | dependent_ids
    recent_rows = (
        await db.execute(
            select(Post, Keyword.phrase)
            .outerjoin(Keyword, Keyword.id == Post.matched_keyword_id)
            .order_by(Post.created_at.desc(), Post.id.desc())
            .limit(DUPLICATE_CANDIDATE_LIMIT)
        )
    ).all()
    relevant_rows = {
        post.id: (post, phrase)
        for post, phrase in recent_rows
    }
    missing_ids = included_ids - relevant_rows.keys()
    if missing_ids:
        extra_rows = (
            await db.execute(
                select(Post, Keyword.phrase)
                .outerjoin(Keyword, Keyword.id == Post.matched_keyword_id)
                .where(Post.id.in_(missing_ids))
            )
        ).all()
        relevant_rows.update({post.id: (post, phrase) for post, phrase in extra_rows})

    posts = [post for post, _phrase in relevant_rows.values()]
    verified_text_posts = [
        post for post in posts
        if post.text
        and extraction_diagnostic(post.raw_data, post.threads_post_id).get("status") == "success"
    ]
    ordered = sorted(verified_text_posts, key=lambda post: (post.created_at, post.id))
    assignments = await asyncio.to_thread(
        _duplicate_assignments,
        [(post.id, post.text) for post in ordered],
    )
    changed_relevance_ids = set(affected_post_ids)
    for post in posts:
        extraction_status = extraction_diagnostic(post.raw_data, post.threads_post_id).get("status")
        if extraction_status == "success":
            duplicate_of_id = assignments.get(post.id)
            was_duplicate = post.is_duplicate
            post.is_duplicate = duplicate_of_id is not None
            post.duplicate_of_id = duplicate_of_id
            if was_duplicate != post.is_duplicate:
                changed_relevance_ids.add(post.id)
        elif extraction_status in CONFIRMED_STATUSES:
            post.is_duplicate = False
            post.duplicate_of_id = None
            post.is_relevant = True
        else:
            post.is_duplicate = False
            post.duplicate_of_id = None
            post.is_relevant = True

    relevance_targets: list[tuple[Post, str]] = []
    for post_id in changed_relevance_ids:
        pair = relevant_rows.get(post_id)
        if pair is None:
            log_event(
                logger,
                logging.INFO,
                "relevance.filter_skipped",
                run_id=run_id,
                source=source,
                post_pk=post_id,
                reason="post_not_loaded",
            )
            continue
        post, phrase = pair
        log_fields = {
            "run_id": run_id,
            "source": source,
            "post_pk": post.id,
            "post_id": post.threads_post_id,
            "keyword_id": post.matched_keyword_id,
            "keyword": phrase or "",
        }
        extraction_status = extraction_diagnostic(post.raw_data, post.threads_post_id).get("status")
        if extraction_status != "success":
            log_event(
                logger,
                logging.INFO,
                "relevance.filter_skipped",
                **log_fields,
                reason="extraction_not_success",
                extraction_status=extraction_status,
                final_relevant=post.is_relevant,
            )
            continue
        matched_stop_word = _matched_stop_word(post.text, context.stop_words)
        post.is_relevant = matched_stop_word is None
        if matched_stop_word is not None:
            log_event(
                logger,
                logging.INFO,
                "relevance.filter_applied",
                **log_fields,
                stage="stop_words",
                matched_stop_word=matched_stop_word,
                decision=False,
            )
            continue
        if post.is_duplicate:
            log_event(
                logger,
                logging.INFO,
                "relevance.filter_skipped",
                **log_fields,
                reason="duplicate",
                duplicate_of_id=post.duplicate_of_id,
                final_relevant=post.is_relevant,
            )
            continue
        if not context.jev_relevance_enabled:
            reason = "jev_disabled"
        elif not context.openrouter_api_key:
            reason = "api_key_missing"
        else:
            relevance_targets.append((post, phrase or ""))
            continue
        log_event(
            logger,
            logging.INFO,
            "relevance.filter_skipped",
            **log_fields,
            reason=reason,
            final_relevant=post.is_relevant,
        )

    # Re-evaluate only recovered/new canonical posts and dependents that became
    # canonical, rather than re-sending all historical posts to the Jev API.
    semaphore = asyncio.Semaphore(JEV_REPROCESS_CONCURRENCY)

    async def decide(post: Post, phrase: str) -> bool | None:
        async with semaphore:
            return await _jev_relevance_decision(
                post.text,
                phrase,
                context,
                post_pk=post.id,
                post_id=post.threads_post_id,
                keyword_id=post.matched_keyword_id,
                run_id=run_id,
                source=source,
                fallback_is_relevant=post.is_relevant,
            )

    decisions = await asyncio.gather(*(decide(post, phrase) for post, phrase in relevance_targets))
    for (post, _phrase), decision in zip(relevance_targets, decisions, strict=True):
        if decision is not None:
            post.is_relevant = decision
        log_event(
            logger,
            logging.INFO,
            "relevance.filter_final",
            run_id=run_id,
            source=source,
            post_pk=post.id,
            post_id=post.threads_post_id,
            keyword_id=post.matched_keyword_id,
            keyword=_phrase,
            stage="jev" if decision is not None else "jev_fallback",
            decision=post.is_relevant,
        )


async def reprocess_recent_posts_for_relevance() -> None:
    """Recheck recent non-duplicate posts with the currently saved Jev prompt."""
    run_id = uuid.uuid4().hex
    started = time.perf_counter()
    try:
        await _reprocess_recent_posts_for_relevance(run_id, started)
    except Exception as error:
        log_event(
            logger,
            logging.ERROR,
            "jev.reprocess_failed",
            run_id=run_id,
            exception_type=type(error).__name__,
            duration_ms=round((time.perf_counter() - started) * 1000),
            exc_info=not isinstance(error, SQLAlchemyError),
        )
        raise


async def _reprocess_recent_posts_for_relevance(run_id: str, started: float) -> None:
    """Run one correlated Jev prompt reprocessing batch."""
    async with SessionFactory() as db:
        stop_words = tuple((await db.scalars(select(StopWord.word_or_phrase))).all())
        api_key = await get_setting(db, "openrouter_api_key")
        context = FilterContext(
            stop_words=stop_words,
            openrouter_api_key=api_key if isinstance(api_key, str) and api_key else None,
            jev_relevance_enabled=bool(await get_setting(db, "jev_relevance_enabled")),
            jev_relevance_prompt=await get_setting(db, "jev_relevance_prompt") or DEFAULT_JEV_RELEVANCE_PROMPT,
        )
        if not context.jev_relevance_enabled or not context.openrouter_api_key:
            log_event(
                logger,
                logging.INFO,
                "jev.reprocess_skipped",
                run_id=run_id,
                reason="jev_disabled" if not context.jev_relevance_enabled else "api_key_missing",
            )
            return

        cutoff = datetime.now(timezone.utc) - timedelta(days=JEV_REPROCESS_DAYS)
        rows = (
            await db.execute(
                select(Post, Keyword.phrase)
                .join(Keyword, Keyword.id == Post.matched_keyword_id)
                .where(
                    Post.is_duplicate.is_(False),
                    Post.text != "",
                    func.coalesce(Post.post_timestamp, Post.created_at) >= cutoff,
                )
                .order_by(func.coalesce(Post.post_timestamp, Post.created_at).desc())
            )
        ).all()

        prompt_fingerprint = hashlib.sha256(context.jev_relevance_prompt.encode("utf-8")).hexdigest()[:12]
        log_event(
            logger,
            logging.INFO,
            "jev.reprocess_started",
            run_id=run_id,
            model=JEV_MODEL,
            prompt_sha256=prompt_fingerprint,
            posts=len(rows),
            cutoff=cutoff.isoformat(),
        )

        semaphore = asyncio.Semaphore(JEV_REPROCESS_CONCURRENCY)

        checked = 0
        failed = 0
        skipped_stop_words = 0
        for start in range(0, len(rows), JEV_REPROCESS_CONCURRENCY):
            batch = rows[start : start + JEV_REPROCESS_CONCURRENCY]
            needs_jev: list[tuple[Post, str]] = []
            for post, phrase in batch:
                matched_stop_word = _matched_stop_word(post.text, context.stop_words)
                if matched_stop_word is not None:
                    post.is_relevant = False
                    checked += 1
                    skipped_stop_words += 1
                    log_event(
                        logger,
                        logging.INFO,
                        "relevance.filter_applied",
                        run_id=run_id,
                        source="prompt_reprocess",
                        post_pk=post.id,
                        post_id=post.threads_post_id,
                        keyword_id=post.matched_keyword_id,
                        keyword=phrase,
                        stage="stop_words",
                        matched_stop_word=matched_stop_word,
                        decision=False,
                    )
                else:
                    needs_jev.append((post, phrase))

            async def decide(post: Post, phrase: str) -> bool | None:
                async with semaphore:
                    return await _jev_relevance_decision(
                        post.text,
                        phrase,
                        context,
                        post_pk=post.id,
                        post_id=post.threads_post_id,
                        keyword_id=post.matched_keyword_id,
                        run_id=run_id,
                        source="prompt_reprocess",
                        fallback_is_relevant=post.is_relevant,
                    )

            decisions = await asyncio.gather(*(decide(post, phrase) for post, phrase in needs_jev))
            for (post, _phrase), decision in zip(needs_jev, decisions, strict=True):
                if decision is None:
                    failed += 1
                    stage = "jev_fallback"
                else:
                    post.is_relevant = decision
                    checked += 1
                    stage = "jev"
                log_event(
                    logger,
                    logging.INFO,
                    "relevance.filter_final",
                    run_id=run_id,
                    source="prompt_reprocess",
                    post_pk=post.id,
                    post_id=post.threads_post_id,
                    keyword_id=post.matched_keyword_id,
                    keyword=_phrase,
                    stage=stage,
                    decision=post.is_relevant,
                )
            await db.commit()

        log_event(
            logger,
            logging.INFO,
            "jev.reprocess_finished",
            run_id=run_id,
            model=JEV_MODEL,
            posts=len(rows),
            checked=checked,
            failed=failed,
            skipped_stop_words=skipped_stop_words,
            days=JEV_REPROCESS_DAYS,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
