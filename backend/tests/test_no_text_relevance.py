"""Posts without usable text must never reach the relevance model."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Post
from app.services.collector import _new_post, _apply_confirmed_extraction, _apply_unconfirmed_extraction
from app.services.filter_service import FilterContext, apply_post_filters
from app.services.threads_post_parser import ExtractionStatus, PostExtraction


class NoTextRelevanceTests(unittest.TestCase):
    def test_empty_and_whitespace_posts_skip_jev(self):
        for text in ("", " \n\t "):
            with self.subTest(text=text):
                post = Post(threads_post_id="123", text=text, is_relevant=True,
                            is_duplicate=True, duplicate_of_id=42, raw_data={})
                context = FilterContext(stop_words=(), jev_relevance_enabled=True,
                                        openrouter_api_key="test")
                with patch("app.services.filter_service._jev_relevance_decision", new_callable=AsyncMock) as decide:
                    asyncio.run(apply_post_filters(post, context))
                    decide.assert_not_awaited()
                self.assertFalse(post.is_relevant)
                self.assertFalse(post.is_duplicate)
                self.assertIsNone(post.duplicate_of_id)

    def test_collection_marks_textless_results_irrelevant(self):
        for status in (ExtractionStatus.NO_TEXT, ExtractionStatus.UNAVAILABLE):
            with self.subTest(status=status):
                extraction = PostExtraction(post_id="123", username="author", permalink="https://www.threads.net/@author/post/123",
                                            text=None, status=status, source="test", parser_version="test")
                post = _new_post(extraction, 1)
                self.assertFalse(post.is_relevant)
                post.is_relevant = True
                if extraction.confirmed:
                    _apply_confirmed_extraction(post, extraction)
                else:
                    _apply_unconfirmed_extraction(post, extraction)
                self.assertFalse(post.is_relevant)
