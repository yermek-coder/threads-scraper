"""Search-card collection and engagement persistence tests."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from unittest.mock import AsyncMock, patch

from sqlalchemy import ScalarResult, create_engine
from sqlalchemy.orm import Session, SessionTransaction

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models import Keyword, Post
from app.services.collector import collect_keyword
from app.services.threads_discovery import DiscoveredPost
from app.services.threads_post_parser import (
    SEARCH_DOM_SOURCE,
    SEARCH_PARSER_VERSION,
    ExtractionStatus,
    PostExtraction,
    add_extraction_diagnostic,
    extraction_is_currently_confirmed,
)
from app.services.threads_web_client import DiscoveryResult


class _NestedTransaction:
    """Adapt a synchronous SQLite savepoint to the collector's async interface."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.transaction: SessionTransaction | None = None

    async def __aenter__(self) -> Self:
        self.transaction = self.session.begin_nested()
        self.transaction.__enter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        assert self.transaction is not None
        return self.transaction.__exit__(exc_type, exc_value, traceback)


class _AsyncSessionAdapter:
    """Keep the production ORM statements while using an in-memory database."""

    def __init__(self, session: Session) -> None:
        self.session = session

    async def scalars(self, statement: Any) -> ScalarResult[Any]:
        return self.session.scalars(statement)

    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction(self.session)

    def add(self, item: object) -> None:
        self.session.add(item)

    async def flush(self) -> None:
        self.session.flush()

    async def commit(self) -> None:
        self.session.commit()


def _candidate(
    post_id: str = "Search1",
    *,
    likes: int | None = None,
    replies: int | None = None,
    reposts: int | None = None,
) -> DiscoveredPost:
    return DiscoveredPost(
        post_id=post_id,
        username="author",
        permalink=f"https://www.threads.com/@author/post/{post_id}",
        likes_count=likes,
        replies_count=replies,
        reposts_count=reposts,
    )


def _extraction(candidate: DiscoveredPost, status: ExtractionStatus = ExtractionStatus.SUCCESS) -> PostExtraction:
    return PostExtraction(
        post_id=candidate.post_id,
        username=candidate.username,
        permalink=candidate.permalink,
        text="Текст из поиска" if status == ExtractionStatus.SUCCESS else None,
        status=status,
        source=SEARCH_DOM_SOURCE,
        parser_version=SEARCH_PARSER_VERSION,
        reason="identity_checked" if status == ExtractionStatus.SUCCESS else "card_unavailable",
    )


class CollectorSearchPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.db = _AsyncSessionAdapter(self.session)
        self.keyword = Keyword(phrase="стоматология", is_active=True)
        self.recalculate_count = 0
        self.session.add(self.keyword)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def collect(self, discovery: DiscoveryResult) -> dict[str, int | bool | str | None]:
        with (
            patch("app.services.collector.get_setting", new_callable=AsyncMock, return_value="web"),
            patch("app.services.collector.discover_keyword_posts", new_callable=AsyncMock, return_value=discovery),
            patch("app.services.collector.recalculate_post_filter_state", new_callable=AsyncMock) as recalculate,
            patch("app.services.collector.send_posts", new_callable=AsyncMock),
            patch("app.services.threads_web_client.fetch_post_pages", new_callable=AsyncMock) as fetch_pages,
        ):
            result = asyncio.run(collect_keyword(self.db, self.keyword, notify=False))
            fetch_pages.assert_not_awaited()
            self.recalculate_count = recalculate.await_count
            return result

    def test_new_post_uses_search_card_text_and_engagement(self) -> None:
        candidate = _candidate(likes=12, replies=3, reposts=4)
        discovery = DiscoveryResult([candidate], {candidate.post_id: _extraction(candidate)}, False, 2, "stable")

        result = self.collect(discovery)
        post = self.session.query(Post).one()

        self.assertEqual((result["scanned"], result["saved"], result["extraction_failures"]), (1, 1, 0))
        self.assertEqual(post.text, "Текст из поиска")
        self.assertEqual((post.likes_count, post.replies_count), (12, 3))
        self.assertEqual(
            post.raw_data["search_engagement"],
            {"reposts_count": 4, "likes_count": 12, "replies_count": 3},
        )
        self.assertTrue(extraction_is_currently_confirmed(post.raw_data, candidate.post_id))
        self.assertEqual(self.recalculate_count, 1)

    def test_context_only_change_rechecks_existing_confirmed_reply(self) -> None:
        candidate = _candidate()
        extraction = _extraction(candidate)
        post = Post(
            threads_post_id=candidate.post_id,
            text=extraction.text,
            author_username=candidate.username,
            permalink=candidate.permalink,
            raw_data={
                "source": SEARCH_DOM_SOURCE,
                "extraction": extraction.diagnostic(),
            },
            matched_keyword_id=self.keyword.id,
        )
        self.session.add(post)
        self.session.commit()
        extraction.conversation_context = [
            {
                "post_id": "Parent",
                "username": "parent",
                "text": "Parent topic",
                "permalink": "https://www.threads.com/@parent/post/Parent",
            }
        ]
        result = self.collect(
            DiscoveryResult([candidate], {candidate.post_id: extraction}, True, 1)
        )
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["saved"], 0)
        self.assertEqual(
            post.raw_data["conversation_context"], extraction.conversation_context
        )
        self.assertEqual(self.recalculate_count, 1)
        self.collect(
            DiscoveryResult([candidate], {candidate.post_id: extraction}, True, 1)
        )
        self.assertEqual(self.recalculate_count, 0)

    def test_confirmed_row_preserves_text_and_known_counts_when_card_omits_them(self) -> None:
        original = _candidate(likes=8, replies=6, reposts=4)
        post = Post(
            threads_post_id=original.post_id,
            text="Подтверждённый текст",
            author_username=original.username,
            permalink=original.permalink,
            likes_count=8,
            replies_count=6,
            raw_data={
                "source": SEARCH_DOM_SOURCE,
                "extraction": _extraction(original).diagnostic(),
                "search_engagement": {"reposts_count": 4, "likes_count": 8, "replies_count": 6},
            },
        )
        self.session.add(post)
        self.session.commit()
        current = _candidate(replies=7)
        discovery = DiscoveryResult([current], {current.post_id: _extraction(current, ExtractionStatus.INCOMPLETE)}, False, 1)

        result = self.collect(discovery)

        self.assertEqual((result["saved"], result["updated"], result["extraction_failures"]), (0, 0, 0))
        self.assertEqual(post.text, "Подтверждённый текст")
        self.assertEqual((post.likes_count, post.replies_count), (8, 7))
        self.assertEqual(
            post.raw_data["search_engagement"],
            {"reposts_count": 4, "likes_count": 8, "replies_count": 7},
        )
        self.assertEqual(self.recalculate_count, 0)

    def test_failed_search_extraction_preserves_safe_counts_and_strips_other_raw_data(self) -> None:
        candidate = _candidate(likes=5, reposts=2)
        post = Post(
            threads_post_id=candidate.post_id,
            text="unverified legacy text",
            author_username=candidate.username,
            permalink=candidate.permalink,
            likes_count=9,
            replies_count=11,
            raw_data={"secret": "discard", "search_engagement": {"reposts_count": 1, "secret": "discard"}},
        )
        self.session.add(post)
        self.session.commit()
        discovery = DiscoveryResult([candidate], {candidate.post_id: _extraction(candidate, ExtractionStatus.INCOMPLETE)}, False, 1)

        result = self.collect(discovery)

        self.assertEqual(result["extraction_failures"], 1)
        self.assertEqual(post.text, "")
        self.assertEqual((post.likes_count, post.replies_count), (5, 11))
        self.assertEqual(
            post.raw_data["search_engagement"],
            {"reposts_count": 2, "likes_count": 5, "replies_count": None},
        )
        self.assertNotIn("secret", post.raw_data)

    def test_identity_mismatch_does_not_save_foreign_search_text(self) -> None:
        candidate = _candidate(likes=2)
        foreign = _extraction(_candidate("OtherPost"))
        discovery = DiscoveryResult([candidate], {candidate.post_id: foreign}, False, 1)

        result = self.collect(discovery)
        post = self.session.query(Post).one()

        self.assertEqual(result["extraction_failures"], 1)
        self.assertEqual(post.threads_post_id, candidate.post_id)
        self.assertEqual(post.text, "")
        self.assertEqual(post.raw_data["extraction"]["reason"], "search_card_extraction_identity_mismatch")
        self.assertEqual(post.likes_count, 2)

    def test_diagnostic_keeps_only_safe_search_engagement(self) -> None:
        candidate = _candidate()
        raw_data = {
            "access_token": "discard",
            "search_engagement": {
                "reposts_count": 6,
                "likes_count": True,
                "replies_count": -1,
                "request_headers": "discard",
            },
        }

        updated = add_extraction_diagnostic(raw_data, _extraction(candidate))

        self.assertEqual(
            updated["search_engagement"],
            {"reposts_count": 6, "likes_count": None, "replies_count": None},
        )
        self.assertNotIn("access_token", updated)

        failed_retry = add_extraction_diagnostic(updated, _extraction(candidate, ExtractionStatus.INCOMPLETE))
        self.assertEqual(failed_retry["search_engagement"], updated["search_engagement"])
        self.assertEqual(failed_retry["extraction"]["status"], ExtractionStatus.SUCCESS.value)
        self.assertEqual(failed_retry["extraction_last_attempt"]["status"], ExtractionStatus.INCOMPLETE.value)


if __name__ == "__main__":
    unittest.main()
