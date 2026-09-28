"""Browser-backed checks for capture on a virtualized Threads search page."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.threads_post_parser import SEARCH_DOM_SOURCE, ExtractionStatus
from app.services.threads_web_client import (
    _SEARCH_DISCOVERY_SCRIPT,
    _SEARCH_STATE_CHANGED_SCRIPT,
    _merge_search_cards,
)


def card(post_id: str, *, text: str, likes: str = "", more: bool = False, more_label: str = "See more") -> str:
    return f"""
    <div data-pressable-container="true">
      <div class="header">
        <a href="/@author">author</a>
        <a href="/@author/post/{post_id}"><time datetime="2026-09-27T10:00:00Z">2h</time></a>
      </div>
      <div dir="auto">{text}</div>
      {f'<button>{more_label}</button>' if more else ''}
      <div role="button"><svg><title>Like</title></svg><span>{likes}</span></div>
      <div role="button"><svg><title>Comment</title></svg><span>3</span></div>
      <div role="button"><svg><title>Repost</title></svg><span>2</span></div>
      <div data-pressable-container="true">
        <div class="header">
          <a href="/@other">other</a>
          <a href="/@other/post/Quoted"><time datetime="2026-09-26T10:00:00Z">1d</time></a>
        </div>
        <div dir="auto">Quoted text must stay out</div>
        <div role="button"><svg><title>Like</title></svg><span>999K</span></div>
      </div>
    </div>
    """


class SearchCardBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from playwright.sync_api import sync_playwright
            cls.playwright = sync_playwright().start()
            executable_path = os.environ.get("THREADS_TEST_CHROMIUM")
            cls.browser = cls.playwright.chromium.launch(
                headless=True,
                executable_path=executable_path,
            )
        except Exception as exc:
            if getattr(cls, "playwright", None) is not None:
                cls.playwright.stop()
            raise unittest.SkipTest(f"Chromium unavailable: {type(exc).__name__}") from exc

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self) -> None:
        self.page = self.browser.new_page()

    def tearDown(self) -> None:
        self.page.close()

    def _render(self, *cards: str) -> dict:
        self.page.set_content(
            '<base href="https://www.threads.com/">'
            '<main aria-label="Column body">' + "".join(cards) + "</main>"
        )
        return self.page.evaluate(_SEARCH_DISCOVERY_SCRIPT)

    def test_card_body_and_counts_are_captured_without_quote_noise(self) -> None:
        state = self._render(card("Main", text="Own full text 🧵", likes="1.2K"))
        self.assertTrue(state["structure"])
        self.assertEqual(len(state["posts"]), 1)
        item = state["posts"][0]
        self.assertEqual((item["id"], item["username"]), ("Main", "author"))
        self.assertEqual(
            (item["likes_count"], item["replies_count"], item["reposts_count"]),
            (1200, 3, 2),
        )
        self.assertFalse(self.page.evaluate(
            _SEARCH_STATE_CHANGED_SCRIPT,
            {
                "signature": state["signature"],
                "scrollHeight": state["scrollHeight"],
                "isLoading": state["is_loading"],
            },
        ))
        found, extractions = {}, {}
        self.assertEqual(_merge_search_cards(found, extractions, state), 1)
        self.assertEqual(extractions["Main"].status, ExtractionStatus.SUCCESS)
        self.assertEqual(extractions["Main"].text, "Own full text 🧵")
        self.assertEqual(extractions["Main"].source, SEARCH_DOM_SOURCE)
        self.assertNotIn("Quoted", found)

    def test_search_result_preserves_reply_context_without_merging_bodies(self) -> None:
        state = self._render(
            '<div data-pagelet="threads_search_results_0">'
            + card("Parent", text="Parent topic", likes="677")
            + card("Reply", text="Own reply", likes="1")
            + "</div>"
        )
        found, extractions = {}, {}
        _merge_search_cards(found, extractions, state)
        self.assertEqual(set(found), {"Parent", "Reply"})
        self.assertIsNone(extractions["Parent"].conversation_context)
        self.assertEqual(extractions["Reply"].text, "Own reply")
        self.assertEqual(extractions["Reply"].likes_count, 1)
        self.assertEqual(
            extractions["Reply"].conversation_context,
            [
                {
                    "post_id": "Parent",
                    "username": "author",
                    "permalink": "https://www.threads.com/@author/post/Parent",
                    "text": "Parent topic",
                }
            ],
        )
        self.assertNotIn("Quoted", found)

    def test_adjacent_search_results_are_not_reply_context(self) -> None:
        state = self._render(
            '<div data-pagelet="threads_search_results_0">'
            + card("First", text="First")
            + "</div>",
            '<div data-pagelet="threads_search_results_1">'
            + card("Second", text="Second")
            + "</div>",
        )
        found, extractions = {}, {}
        _merge_search_cards(found, extractions, state)
        self.assertTrue(
            all(p.conversation_context is None for p in extractions.values())
        )

    def test_multiple_displayed_ancestors_keep_order(self) -> None:
        state = self._render(
            '<div data-pagelet="threads_search_results_0">'
            + card("Root", text="Root")
            + card("Parent", text="Parent")
            + card("Reply", text="Reply")
            + "</div>"
        )
        found, extractions = {}, {}
        _merge_search_cards(found, extractions, state)
        self.assertEqual(
            [p["post_id"] for p in extractions["Reply"].conversation_context],
            ["Root", "Parent"],
        )

    def test_truncated_parent_is_not_used_as_verified_context(self) -> None:
        state = self._render(
            '<div data-pagelet="threads_search_results_0">'
            + card("Parent", text="Visible prefix", more=True)
            + card("Reply", text="Reply")
            + "</div>"
        )
        found, extractions = {}, {}
        _merge_search_cards(found, extractions, state)
        self.assertEqual(extractions["Reply"].status, ExtractionStatus.SUCCESS)
        self.assertIsNone(extractions["Reply"].conversation_context)

    def test_context_survives_later_standalone_observation(self) -> None:
        found, extractions = {}, {}
        _merge_search_cards(
            found,
            extractions,
            self._render(
                '<div data-pagelet="threads_search_results_0">'
                + card("Parent", text="Parent")
                + card("Reply", text="Reply")
                + "</div>"
            ),
        )
        _merge_search_cards(
            found, extractions, self._render(card("Reply", text="Reply", likes="2"))
        )
        self.assertEqual(
            extractions["Reply"].conversation_context[0]["post_id"], "Parent"
        )
        self.assertEqual(extractions["Reply"].likes_count, 2)

    def test_oracle_conversation_fixture_keeps_parent_and_reply_separate(self) -> None:
        html = (
            Path(__file__).parent / "fixtures/threads/search_conversation.html"
        ).read_text()
        state = self._render(html)
        found, extractions = {}, {}
        _merge_search_cards(found, extractions, state)
        self.assertEqual(set(found), {"RootPost", "ReplyPost"})
        reply = extractions["ReplyPost"]
        root = extractions["RootPost"]
        self.assertEqual(reply.status, ExtractionStatus.SUCCESS)
        self.assertEqual(root.status, ExtractionStatus.SUCCESS)
        self.assertIn("Сейчас собираюсь подавать в суд", reply.text)
        self.assertNotIn("И вот 5 вещей", reply.text)
        self.assertEqual(reply.conversation_context[0]["text"], root.text)
        self.assertEqual(reply.conversation_context[0]["post_id"], "RootPost")
        self.assertEqual((root.likes_count, reply.likes_count), (677, 1))

    def test_permalink_does_not_need_to_wrap_timestamp(self) -> None:
        html = card("DetachedTime", text="Text survives detached timestamp")
        html = html.replace(
            '<a href="/@author/post/DetachedTime"><time datetime="2026-09-27T10:00:00Z">2h</time></a>',
            '<a href="/@author/post/DetachedTime">post</a><time datetime="2026-09-27T10:00:00Z">2h</time>',
        )

        state = self._render(html)
        found, extractions = {}, {}
        _merge_search_cards(found, extractions, state)

        self.assertEqual(list(found), ["DetachedTime"])
        self.assertEqual(extractions["DetachedTime"].text, "Text survives detached timestamp")
        self.assertEqual(extractions["DetachedTime"].timestamp, "2026-09-27T10:00:00Z")

    def test_virtualized_away_post_and_best_observation_are_retained(self) -> None:
        found, extractions = {}, {}
        first = self._render(card("First", text="First body", likes="4"))
        _merge_search_cards(found, extractions, first)
        second = self._render(
            card("First", text="First body extended", likes="5"),
            card("Second", text="Second body", likes="8"),
        )
        _merge_search_cards(found, extractions, second)
        third = self._render(card("Second", text="Second body", likes="7"))
        _merge_search_cards(found, extractions, third)
        self.assertEqual(set(found), {"First", "Second"})
        self.assertEqual(found["First"].likes_count, 5)
        self.assertEqual(extractions["First"].text, "First body extended")

    def test_localized_controls_and_svg_aria_labels_are_read(self) -> None:
        html = card("Localized", text="Текст", likes="1,2 тыс.")
        html = html.replace(
            '<div role="button"><svg><title>Like</title></svg>',
            '<div role="button" aria-label="Нравится"><svg aria-label="Like"></svg>',
        ).replace(
            '<div role="button"><svg><title>Comment</title></svg>',
            '<div role="button"><svg aria-label="Комментировать"></svg>',
        ).replace(
            '<div role="button"><svg><title>Repost</title></svg>',
            '<div role="button" aria-label="Репост"><svg></svg>',
        )
        state = self._render(html)
        item = state["posts"][0]
        self.assertEqual(
            (item["likes_count"], item["replies_count"], item["reposts_count"]),
            (1200, 3, 2),
        )

    def test_article_wrapping_pressable_card_is_not_discarded(self) -> None:
        state = self._render(f'<article>{card("Wrapped", text="Wrapped body", likes="4")}</article>')
        found, extractions = {}, {}
        _merge_search_cards(found, extractions, state)
        self.assertEqual(list(found), ["Wrapped"])
        self.assertEqual(extractions["Wrapped"].text, "Wrapped body")

    def test_truncated_card_is_not_confirmed_as_full_text(self) -> None:
        state = self._render(card("Truncated", text="Visible prefix", more=True))
        found, extractions = {}, {}
        _merge_search_cards(found, extractions, state)
        self.assertEqual(extractions["Truncated"].status, ExtractionStatus.INCOMPLETE)
        self.assertEqual(extractions["Truncated"].reason, "search_card_text_truncated")
        self.assertEqual(extractions["Truncated"].timestamp, "2026-09-27T10:00:00Z")

        unicode_state = self._render(card("UnicodeMore", text="Visible prefix", more=True, more_label="… See more"))
        _merge_search_cards(found, extractions, unicode_state)
        self.assertEqual(extractions["UnicodeMore"].reason, "search_card_text_truncated")

    def test_link_preview_inside_body_does_not_leak_preview_title(self) -> None:
        state = self._render(card(
            "Preview",
            text='Own text <a href="https://example.com"><img alt="" src="data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=">Preview title</a>',
        ))
        found, extractions = {}, {}
        _merge_search_cards(found, extractions, state)
        self.assertEqual(extractions["Preview"].status, ExtractionStatus.SUCCESS)
        self.assertEqual(extractions["Preview"].text, "Own text ")

    def test_multiple_paragraphs_with_untranslated_external_links(self) -> None:
        # Reduced from DO6Kh4DkgQl captured on the Oracle search page (ru-RU).
        for locale, column_label in (("en-US", "Column body"), ("ru-RU", "Содержимое столбца")):
            with self.subTest(locale=locale):
                context = self.browser.new_context(locale=locale)
                try:
                    page = context.new_page()
                    html = card("ExternalLinks", text="2016 yil Toshkent")
                    html = html.replace(
                        '<div dir="auto">2016 yil Toshkent</div>',
                        '<div dir="auto">2016 yil Toshkent</div>'
                        '<div dir="auto">Clinic course.</div>'
                        '<span dir="auto"><a href="https://l.threads.com/?u=http%3A%2F%2Fexample.com">'
                        '<span translate="no">example.com</span></a></span>'
                        '<span dir="auto"><a href="https://example.org">'
                        '<span translate="no">example.org</span></a></span>',
                    )
                    page.set_content('<base href="https://www.threads.com/">'
                                     f'<main aria-label="{column_label}">{html}</main>')
                    state = page.evaluate(_SEARCH_DISCOVERY_SCRIPT)
                    found, extractions = {}, {}
                    _merge_search_cards(found, extractions, state)
                    self.assertEqual(extractions["ExternalLinks"].status, ExtractionStatus.SUCCESS)
                    self.assertEqual(extractions["ExternalLinks"].text,
                                     "2016 yil Toshkent\nClinic course.\nexample.com\nexample.org")
                finally:
                    context.close()

    def test_localized_reply_metadata_is_excluded_with_external_links(self) -> None:
        for label in ("Replying to", "В ответ"):
            with self.subTest(label=label):
                html = card("ReplyContext", text="Own reply text")
                html = html.replace('<div dir="auto">Own reply text</div>',
                                    f'<span dir="auto">{label} <span translate="no">@original</span></span>'
                                    '<div dir="auto">Own reply text</div>'
                                    '<span dir="auto"><a href="https://example.com">'
                                    '<span translate="no">example.com</span></a></span>')
                state = self._render(html)
                found, extractions = {}, {}
                _merge_search_cards(found, extractions, state)
                self.assertEqual(extractions["ReplyContext"].status, ExtractionStatus.SUCCESS)
                self.assertEqual(extractions["ReplyContext"].text, "Own reply text\nexample.com")

    def test_multiline_body_with_profile_mention_is_preserved(self) -> None:
        html = card("Mention", text="First paragraph")
        html = html.replace('<div dir="auto">First paragraph</div>',
                            '<div dir="auto">First paragraph</div>'
                            '<div dir="auto">Thanks <a href="/@other"><span translate="no">@other</span></a>!</div>')
        state = self._render(html)
        found, extractions = {}, {}
        _merge_search_cards(found, extractions, state)
        self.assertEqual(extractions["Mention"].text, "First paragraph\nThanks @other!")

    def test_reply_metadata_without_body_is_not_saved_as_post_text(self) -> None:
        html = card("MetadataOnly", text='<span>В ответ <span translate="no">@original</span></span>')
        # Remove the quote attachment: metadata alone cannot prove no_text.
        html = html[:html.index('<div data-pressable-container="true">', 10)] + '</div>'
        state = self._render(html)
        found, extractions = {}, {}
        _merge_search_cards(found, extractions, state)
        self.assertEqual(extractions["MetadataOnly"].status, ExtractionStatus.UNKNOWN_STRUCTURE)
        self.assertIsNone(extractions["MetadataOnly"].text)

    def test_unknown_reply_metadata_is_not_combined_with_body(self) -> None:
        html = card("UnknownReply", text="Own reply text")
        html = html.replace('<div dir="auto">Own reply text</div>',
                            '<span dir="auto">Unknown context <span translate="no">@original</span></span>'
                            '<div dir="auto">Own reply text</div>')
        state = self._render(html)
        found, extractions = {}, {}
        _merge_search_cards(found, extractions, state)
        self.assertEqual(extractions["UnknownReply"].status, ExtractionStatus.UNKNOWN_STRUCTURE)


if __name__ == "__main__":
    unittest.main()
