"""Feed responses expose verified conversation bodies without raw metadata."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.posts import _post_read
from app.models import Post


class FeedConversationTests(unittest.TestCase):
    def post(self, status: str = "success") -> Post:
        return Post(
            id=1,
            threads_post_id="Reply",
            text="Own reply",
            author_username="reply",
            permalink="https://www.threads.com/@reply/post/Reply",
            post_timestamp=None,
            likes_count=1,
            replies_count=2,
            matched_keyword_id=None,
            is_relevant=True,
            is_duplicate=False,
            duplicate_of_id=None,
            is_read=False,
            created_at=datetime.now(timezone.utc),
            raw_data={
                "access_token": "discard",
                "extraction": {
                    "post_id": "Reply",
                    "status": status,
                    "source": "threads_search_dom",
                    "parser_version": "threads-search-dom-v4",
                },
                "conversation_context": [
                    {
                        "post_id": "Parent",
                        "username": "parent",
                        "permalink": "https://www.threads.com/@parent/post/Parent",
                        "text": "Parent body",
                        "access_token": "discard",
                    }
                ],
            },
        )

    def test_feed_returns_context_separately_without_private_metadata(self) -> None:
        response = _post_read(self.post(), "keyword").model_dump()
        self.assertEqual(response["text"], "Own reply")
        self.assertEqual(
            response["conversation_context"],
            [
                {
                    "post_id": "Parent",
                    "username": "parent",
                    "permalink": "https://www.threads.com/@parent/post/Parent",
                    "text": "Parent body",
                }
            ],
        )
        self.assertNotIn("discard", str(response))

    def test_unconfirmed_reply_does_not_display_context_as_verified_content(
        self,
    ) -> None:
        response = _post_read(self.post("incomplete"), None)
        self.assertEqual(response.text, "")
        self.assertEqual(response.conversation_context, [])

    def test_legacy_rows_have_empty_context(self) -> None:
        post = self.post()
        post.raw_data.pop("conversation_context")
        self.assertEqual(_post_read(post, None).conversation_context, [])
