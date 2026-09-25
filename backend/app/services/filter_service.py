"""Stop-word filtering and exact/fuzzy duplicate detection."""

import asyncio
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Post, StopWord

FUZZY_DUPLICATE_THRESHOLD = 85
DUPLICATE_CANDIDATE_LIMIT = 2000


@dataclass
class FilterContext:
    """Stop words and lightweight canonical posts cached for one ingestion batch."""

    stop_words: tuple[str, ...]
    duplicate_candidates: list[tuple[int, str]] = field(default_factory=list)


async def load_filter_context(db: AsyncSession) -> FilterContext:
    """Load stop words once and only id/text for the latest canonical posts."""
    stop_words = tuple((await db.scalars(select(StopWord.word_or_phrase))).all())
    rows = (
        await db.execute(
            select(Post.id, Post.text)
            .where(Post.is_duplicate.is_(False), Post.text != "")
            .order_by(Post.created_at.desc())
            .limit(DUPLICATE_CANDIDATE_LIMIT)
        )
    ).all()
    candidates = [(post_id, text) for post_id, text in rows if text]
    return FilterContext(stop_words=stop_words, duplicate_candidates=candidates)


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


async def apply_post_filters(post: Post, context: FilterContext) -> Post:
    """Set relevance and duplicate state using the batch's cached filter context."""
    post.is_relevant = not any(contains_stop_word(post.text, word) for word in context.stop_words)
    normalized = " ".join(post.text.casefold().split())
    if not normalized:
        return post
    duplicate_id = await asyncio.to_thread(_find_duplicate_id, normalized, context.duplicate_candidates)
    if duplicate_id is not None:
        post.is_duplicate = True
        post.duplicate_of_id = duplicate_id
    else:
        context.duplicate_candidates.append((post.id, post.text))
    return post
