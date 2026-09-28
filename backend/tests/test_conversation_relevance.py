"""Search conversation context persists separately and reaches the relevance API."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.collector import _apply_confirmed_extraction, _new_post
from app.services.filter_service import (
    FilterContext,
    _jev_relevance_decision,
    apply_post_filters,
)
from app.services.threads_post_parser import (
    SEARCH_DOM_SOURCE,
    SEARCH_PARSER_VERSION,
    ExtractionStatus,
    PostExtraction,
    add_extraction_diagnostic,
    sanitized_conversation_context,
)

CONTEXT = [
    {
        "post_id": "Parent",
        "username": "parent_author",
        "permalink": "https://www.threads.com/@parent_author/post/Parent",
        "text": "Discussing dental implants",
    }
]


def reply_extraction() -> PostExtraction:
    """Build a confirmed reply with independently extracted ancestors."""
    return PostExtraction(
        post_id="Reply",
        username="reply_author",
        permalink="https://www.threads.com/@reply_author/post/Reply",
        text="Own reply",
        status=ExtractionStatus.SUCCESS,
        source=SEARCH_DOM_SOURCE,
        parser_version=SEARCH_PARSER_VERSION,
        conversation_context=CONTEXT,
    )


class ConversationRelevanceTests(unittest.TestCase):
    def test_context_is_persisted_separately_on_create_and_refresh(self) -> None:
        extraction = reply_extraction()
        post = _new_post(extraction, 1)
        self.assertEqual(post.text, "Own reply")
        self.assertEqual(post.raw_data["conversation_context"], CONTEXT)
        post.raw_data = {**post.raw_data, "search_engagement": {"likes_count": 5}}
        _apply_confirmed_extraction(post, extraction)
        self.assertEqual(post.raw_data["conversation_context"], CONTEXT)
        self.assertEqual(post.raw_data["search_engagement"]["likes_count"], 5)

    def test_failed_retry_keeps_only_safe_verified_context(self) -> None:
        post = _new_post(reply_extraction(), 1)
        post.raw_data = {
            **post.raw_data,
            "conversation_context": [{**CONTEXT[0], "access_token": "discard"}],
        }
        retry = reply_extraction()
        retry.status = ExtractionStatus.INCOMPLETE
        retry.conversation_context = None
        raw_data = add_extraction_diagnostic(post.raw_data, retry)
        self.assertEqual(raw_data["conversation_context"], CONTEXT)
        self.assertEqual(raw_data["extraction"]["status"], "success")

    def test_context_rejects_mismatched_identities_and_self_links(self) -> None:
        for bad in (
            {**CONTEXT[0], "post_id": "Reply"},
            {
                **CONTEXT[0],
                "permalink": "https://example.com/@parent_author/post/Parent",
            },
            {**CONTEXT[0], "username": "foreign"},
            {**CONTEXT[0], "text": None},
        ):
            with self.subTest(context=bad):
                self.assertEqual(
                    sanitized_conversation_context(
                        {"conversation_context": [bad]}, "Reply"
                    ),
                    [],
                )

    def test_filters_pass_context_to_model(self) -> None:
        post = _new_post(reply_extraction(), 1)
        context = FilterContext(
            stop_words=(), jev_relevance_enabled=True, openrouter_api_key="test"
        )
        with patch(
            "app.services.filter_service._jev_relevance_decision",
            new_callable=AsyncMock,
            return_value=False,
        ) as decide:
            asyncio.run(apply_post_filters(post, context, "dentist"))
        self.assertEqual(decide.call_args.kwargs["conversation_context"], CONTEXT)
        self.assertFalse(post.is_relevant)

    def test_api_receives_reply_and_context_in_separate_fields(self) -> None:
        client = AsyncMock()
        client.post.return_value = httpx.Response(
            200, json={"answers": {"relevant": {"type": "noul", "noul": 0.1}}}
        )
        context = FilterContext(
            stop_words=(),
            openrouter_api_key="test",
            jev_relevance_prompt="Custom criteria",
        )
        with patch("app.services.filter_service.httpx.AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            result = asyncio.run(
                _jev_relevance_decision(
                    "Own reply", "dentist", context, conversation_context=CONTEXT
                )
            )
        self.assertFalse(result)
        payload = client.post.call_args.kwargs["json"]
        self.assertEqual(
            payload["state"],
            {
                "post": "Own reply",
                "keyword": "dentist",
                "conversation_context": CONTEXT,
            },
        )
        self.assertIn(
            "Custom criteria", payload["questions"]["relevant"]["instructions"]
        )
        self.assertIn(
            "сама по себе не делает ответ релевантным",
            payload["questions"]["relevant"]["instructions"],
        )

    def test_standalone_api_payload_stays_compatible(self) -> None:
        client = AsyncMock()
        client.post.return_value = httpx.Response(
            200, json={"answers": {"relevant": {"type": "noul", "noul": 0.9}}}
        )
        context = FilterContext(
            stop_words=(),
            openrouter_api_key="test",
            jev_relevance_prompt="Custom criteria",
        )
        with patch("app.services.filter_service.httpx.AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            result = asyncio.run(
                _jev_relevance_decision("Standalone", "dentist", context)
            )
        self.assertTrue(result)
        payload = client.post.call_args.kwargs["json"]
        self.assertEqual(payload["state"], {"post": "Standalone", "keyword": "dentist"})
        self.assertEqual(
            payload["questions"]["relevant"]["instructions"], "Custom criteria"
        )
