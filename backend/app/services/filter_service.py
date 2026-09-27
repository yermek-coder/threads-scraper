"""Stop-word filtering and exact/fuzzy duplicate detection."""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionFactory
from app.models import Keyword, Post, StopWord
from app.services.settings_service import DEFAULT_JEV_RELEVANCE_PROMPT, get_setting
from app.services.threads_post_parser import CONFIRMED_STATUSES, extraction_diagnostic

FUZZY_DUPLICATE_THRESHOLD = 85
DUPLICATE_CANDIDATE_LIMIT = 2000
JEV_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"
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


async def apply_post_filters(post: Post, context: FilterContext, keyword: str = "") -> Post:
    """Set stop-word, duplicate, and optional Jev relevance state for one post."""
    if extraction_diagnostic(post.raw_data, post.threads_post_id).get("status") not in CONFIRMED_STATUSES:
        post.is_relevant = True
        post.is_duplicate = False
        post.duplicate_of_id = None
        return post
    post.is_relevant = not any(contains_stop_word(post.text, word) for word in context.stop_words)
    normalized = " ".join(post.text.casefold().split())
    if normalized:
        duplicate_id = await asyncio.to_thread(_find_duplicate_id, normalized, context.duplicate_candidates)
        if duplicate_id is not None:
            post.is_duplicate = True
            post.duplicate_of_id = duplicate_id
    if (
        post.is_relevant
        and not post.is_duplicate
        and context.jev_relevance_enabled
        and context.openrouter_api_key
    ):
        decision = await _jev_relevance_decision(post.text, keyword, context)
        if decision is not None:
            post.is_relevant = decision
    return post


def remember_post(post: Post, context: FilterContext) -> None:
    """Add a successfully saved canonical post to the current batch's duplicate candidates."""
    if (
        extraction_diagnostic(post.raw_data, post.threads_post_id).get("status") in CONFIRMED_STATUSES
        and not post.is_duplicate
        and " ".join(post.text.casefold().split())
    ):
        context.duplicate_candidates.append((post.id, post.text))


async def _jev_relevance_decision(post_text: str, keyword: str, context: FilterContext) -> bool | None:
    """Ask Jev whether a post is relevant; keep deterministic results if the API fails."""
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
            logger.warning("OpenRouter Jev relevance request failed with HTTP %s", response.status_code)
            return None
        answer = response.json()["answers"]["relevant"]
        probability = float(answer["noul"])
        if answer.get("type") != "noul" or not 0.0 <= probability <= 1.0:
            raise ValueError("Unexpected Jev relevance response")
        return probability >= 0.5
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        logger.warning("OpenRouter Jev relevance check failed: %s", type(error).__name__)
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


async def recalculate_post_filter_state(db: AsyncSession, affected_post_ids: set[int]) -> None:
    """Recompute verified duplicate links and filters after corrected text is saved.

    The recent candidate window matches normal ingestion. Rows outside that
    window are included when they are directly affected by a changed canonical
    post. This function never sends notifications.
    """
    if not affected_post_ids:
        return
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
            continue
        post, phrase = pair
        if extraction_diagnostic(post.raw_data, post.threads_post_id).get("status") != "success":
            continue
        post.is_relevant = not any(contains_stop_word(post.text, word) for word in context.stop_words)
        if post.is_relevant and not post.is_duplicate and context.jev_relevance_enabled and context.openrouter_api_key:
            relevance_targets.append((post, phrase or ""))

    # Re-evaluate only recovered/new canonical posts and dependents that became
    # canonical, rather than re-sending all historical posts to the Jev API.
    semaphore = asyncio.Semaphore(JEV_REPROCESS_CONCURRENCY)

    async def decide(post: Post, phrase: str) -> bool | None:
        async with semaphore:
            return await _jev_relevance_decision(post.text, phrase, context)

    decisions = await asyncio.gather(*(decide(post, phrase) for post, phrase in relevance_targets))
    for (post, _phrase), decision in zip(relevance_targets, decisions, strict=True):
        if decision is not None:
            post.is_relevant = decision


async def reprocess_recent_posts_for_relevance() -> None:
    """Recheck recent non-duplicate posts with the currently saved Jev prompt."""
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
            logger.info("Skipping recent Jev reprocessing: relevance filter or API key is disabled")
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

        semaphore = asyncio.Semaphore(JEV_REPROCESS_CONCURRENCY)

        async def decide(text: str, phrase: str) -> bool | None:
            async with semaphore:
                return await _jev_relevance_decision(text, phrase, context)

        checked = 0
        failed = 0
        for start in range(0, len(rows), JEV_REPROCESS_CONCURRENCY):
            batch = rows[start : start + JEV_REPROCESS_CONCURRENCY]
            needs_jev: list[tuple[Post, str]] = []
            for post, phrase in batch:
                if any(contains_stop_word(post.text, word) for word in context.stop_words):
                    post.is_relevant = False
                    checked += 1
                else:
                    needs_jev.append((post, phrase))

            decisions = await asyncio.gather(*(decide(post.text, phrase) for post, phrase in needs_jev))
            for (post, _phrase), decision in zip(needs_jev, decisions, strict=True):
                if decision is None:
                    failed += 1
                    continue
                post.is_relevant = decision
                checked += 1
            await db.commit()

        logger.info(
            "Recent Jev reprocessing finished: checked=%s failed=%s days=%s",
            checked,
            failed,
            JEV_REPROCESS_DAYS,
        )
