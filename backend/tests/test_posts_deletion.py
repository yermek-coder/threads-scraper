"""Bulk deletion removes hidden posts and respects foreign keys."""
import asyncio
from pathlib import Path
import sys
import unittest

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.posts import delete_all_posts
from app.core.database import Base
from app.models.entities import Keyword, NotificationLog, Post


class PostDeletionTests(unittest.TestCase):
    def test_clear_all_and_repeat_on_empty_database(self):
        engine = create_engine('sqlite:///:memory:')

        @event.listens_for(engine, 'connect')
        def enable_foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')

        Base.metadata.create_all(engine)
        with Session(engine) as session:
            keyword = Keyword(phrase='test')
            session.add(keyword)
            session.flush()
            original = Post(threads_post_id='original', matched_keyword_id=keyword.id)
            session.add(original)
            session.flush()
            session.add(Post(threads_post_id='duplicate', is_duplicate=True,
                             is_relevant=False, is_read=True, duplicate_of_id=original.id))
            session.add(NotificationLog(channel='email', recipient='test@example.com',
                                        status='sent', post_id=original.id))
            session.commit()

            class Adapter:
                async def execute(self, statement):
                    return session.execute(statement)

                async def commit(self):
                    session.commit()

            for _ in range(2):
                asyncio.run(delete_all_posts(Adapter()))
                session.expire_all()
                self.assertEqual(session.scalar(select(func.count(Post.id))), 0)
                self.assertEqual(session.scalar(select(func.count(Keyword.id))), 1)
                log = session.scalar(select(NotificationLog))
                self.assertIsNotNone(log)
                self.assertIsNone(log.post_id)
        engine.dispose()
