"""Feed ordering and engagement response tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session
from sqlalchemy.sql import Executable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.posts import _post_read, list_posts
from app.api.schemas import PostPage
from app.core.database import Base
from app.models import Post


class AsyncSessionAdapter:
    """Run the async route against an in-memory synchronous test database."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.last_query: Executable | None = None

    async def scalar(self, statement: Executable) -> Any:
        return self.session.scalar(statement)

    async def execute(self, statement: Executable) -> Result[Any]:
        self.last_query = statement
        return self.session.execute(statement)


class PostSortingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.db = AsyncSessionAdapter(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def add_post(
        self,
        post_id: str,
        timestamp: datetime | None,
        likes: int,
        replies: int = 0,
        reposts: object | None = None,
    ) -> Post:
        raw_data = {"search_engagement": {"reposts_count": reposts}} if reposts is not None else {}
        post = Post(
            threads_post_id=post_id,
            text="",
            author_username="test_author",
            permalink=f"https://www.threads.com/@test_author/post/{post_id}",
            post_timestamp=timestamp,
            likes_count=likes,
            replies_count=replies,
            raw_data=raw_data,
            is_relevant=True,
            is_duplicate=False,
            is_read=False,
        )
        self.session.add(post)
        self.session.flush()
        return post

    def list_page(self, *, sort_by: str, page: int = 1, page_size: int = 20) -> PostPage:
        return asyncio.run(
            list_posts(
                db=self.db,
                page=page,
                page_size=page_size,
                keyword_id=None,
                unread_only=False,
                hide_irrelevant=False,
                include_duplicates=False,
                q=None,
                sort_by=sort_by,
            )
        )

    def test_engagement_orders_before_pagination_with_stable_ties(self) -> None:
        jan_1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        jan_2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
        jan_3 = datetime(2026, 1, 3, tzinfo=timezone.utc)
        self.add_post("older_reposted", jan_1, likes=4, replies=1, reposts=4)
        self.add_post("newer_low_score", jan_3, likes=2, replies=1)
        self.add_post("tied_lower_id", jan_2, likes=9)
        self.add_post("tied_higher_id", jan_2, likes=9)
        self.add_post("unknown_date", None, likes=9)
        self.session.commit()

        first = self.list_page(sort_by="engagement", page_size=2)
        second = self.list_page(sort_by="engagement", page=2, page_size=2)
        third = self.list_page(sort_by="engagement", page=3, page_size=2)

        self.assertEqual(first.total, 5)
        self.assertEqual([post.threads_post_id for post in first.items], ["tied_higher_id", "tied_lower_id"])
        self.assertEqual([post.threads_post_id for post in second.items], ["older_reposted", "unknown_date"])
        self.assertEqual([post.threads_post_id for post in third.items], ["newer_low_score"])
        self.assertEqual([post.reposts_count for post in second.items], [4, 0])

        sql = str(self.db.last_query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        self.assertIn("json_typeof((posts.raw_data -> 'search_engagement') -> 'reposts_count')", sql)
        self.assertIn("AS BIGINT", sql)
        self.assertIn("^[0-9]{1,18}$", sql)
        self.assertIn("posts.post_timestamp DESC NULLS LAST, posts.id DESC", sql)

    def test_invalid_json_reposts_do_not_break_or_change_engagement_order(self) -> None:
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.add_post("valid", timestamp, likes=1, reposts=2)
        self.add_post("string", timestamp, likes=2, reposts="oops")
        self.add_post("numeric_string", timestamp, likes=2, reposts="9")
        self.add_post("boolean", timestamp, likes=2, reposts=True)
        self.add_post("negative", timestamp, likes=2, reposts=-4)
        self.session.commit()

        page = self.list_page(sort_by="engagement")

        self.assertEqual(page.items[0].threads_post_id, "valid")
        self.assertTrue(all(item.reposts_count == 0 for item in page.items[1:]))

    def test_newest_order_does_not_depend_on_engagement(self) -> None:
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        new = datetime(2026, 1, 2, tzinfo=timezone.utc)
        self.add_post("old_popular", old, likes=100, reposts=20)
        self.add_post("new_quiet", new, likes=0)
        self.session.commit()

        page = self.list_page(sort_by="newest")

        self.assertEqual([post.threads_post_id for post in page.items], ["new_quiet", "old_popular"])
        sql = str(self.db.last_query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        self.assertNotIn("search_engagement", sql)

    def test_response_uses_zero_for_invalid_or_missing_reposts(self) -> None:
        post = self.add_post("missing", None, likes=1)
        self.assertEqual(_post_read(post, None).reposts_count, 0)
        post.raw_data = {"search_engagement": {"reposts_count": -2}}
        self.assertEqual(_post_read(post, None).reposts_count, 0)
        post.raw_data = {"search_engagement": {"reposts_count": 7}}
        self.assertEqual(_post_read(post, None).reposts_count, 7)


if __name__ == "__main__":
    unittest.main()
