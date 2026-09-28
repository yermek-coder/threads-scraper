from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.threads_discovery import (
    DiscoveredPost,
    merge_discovery_batch,
    pagination_round_is_stable,
)
from app.services.threads_post_parser import (
    ExtractionStatus,
    PostExtraction,
    add_extraction_diagnostic,
    extraction_diagnostic,
    extraction_is_currently_confirmed,
    parse_api_post,
    parse_post_card_html,
)

FIXTURES = Path(__file__).parent / "fixtures" / "threads"


def load_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def parse_fixture(name: str, post_id: str, username: str) -> PostExtraction:
    return parse_post_card_html(
        load_html(name),
        expected_post_id=post_id,
        expected_username=username,
        permalink=f"https://www.threads.com/@{username}/post/{post_id}",
    )


class ThreadsPostParserTests(unittest.TestCase):
    def test_control_page_a_preserves_body_lines_emoji_and_order(self) -> None:
        result = parse_fixture("control_a.html", "DcljXqxgCut", "author_a")
        self.assertEqual(result.status, ExtractionStatus.SUCCESS)
        self.assertEqual(
            result.text,
            "Город Усть-каменогорск\n"
            "🦷 Вакансия: Администратор в стоматологию Мы ищем ответственного и приветливого администратора в нашу стоматологическую клинику.\n"
            "Сатпаева 2\n"
            "Клиника [организация скрыта]\n"
            "— Зарплата: (от 250.000 ₸ + бонусы)\n"
            "— Дружный коллектив\n"
            "— Обучение за счёт клиники\n"
            "Пишите (отправляйте своё резюме ) - 📱 [номер скрыт]",
        )

    def test_control_page_b_preserves_list_and_sanitized_lines(self) -> None:
        result = parse_fixture("control_b.html", "DckzmKElyDP", "author_b")
        self.assertEqual(result.status, ExtractionStatus.SUCCESS)
        self.assertEqual(
            result.text,
            "Стоматология предоставляет услуги:\n"
            "* Лечение зубов и слизистой полости рта\n"
            "* Протезирование зубов\n"
            "* Удаление зубов\n"
            "*Пенсионерам скидка до 10%*\n"
            "г. Усть-Каменогорск, пр. Назарбаева, 86 (район востокмашзавод)\n"
            "Тел.: [номер скрыт]\n"
            "[номер скрыт]\n"
            "[имя скрыто]",
        )

    def test_real_video_post_preserves_text_emoji_and_hashtag_order(self) -> None:
        result = parse_fixture("control_c.html", "Db7rl2_jjXp", "author_c")
        self.assertEqual(result.status, ExtractionStatus.SUCCESS)
        self.assertEqual(
            result.text,
            "МЫ УСТАНОВИЛИ 3 КАБИНЕТА ПОД КЛЮЧ В ГОРОДЕ УСТЬ-КАМЕНОГОРСК! 🦷😍 ЕСЛИ ВЫ ТОЖЕ ХОТИТЕ ЗАКАЗАТЬ СТОМАТОЛОГИЧЕСКИЕ ТОВАРЫ ПО САМЫМ ВЫГОДНЫМ ЦЕНАМ, ПИШИТЕ ПО НОМЕРУ НИЖЕ ✍️ 📞: [номер скрыт]\n"
            "📍: [адрес скрыт]\n"
            "🇰🇿: ДОСТАВКА ПО ВСЕМУ КАЗАХСТАНУ #reels #стоматология #стоматолог #reels #казахстан #стоматологическаяклиника #стоматолог #reelsviral #reelsviral #стоматология #стоматолог",
        )

    def test_short_number_and_date_are_valid_content(self) -> None:
        number = parse_fixture("short_text.html", "ShortText1", "short_user")
        date = parse_fixture("date_text.html", "DateText1", "short_user")
        self.assertEqual((number.status, number.text), (ExtractionStatus.SUCCESS, "1"))
        self.assertEqual((date.status, date.text), (ExtractionStatus.SUCCESS, "2026-09-26"))

    def test_nested_quote_is_excluded_and_own_text_remains(self) -> None:
        result = parse_fixture("quoted_post.html", "MainPost1", "main_user")
        self.assertEqual((result.status, result.text), (ExtractionStatus.SUCCESS, "Свой текст ✨"))
        self.assertNotIn("Вложенный текст", result.text or "")

    def test_recognized_reply_context_is_excluded_from_own_text(self) -> None:
        result = parse_post_card_html(
            load_html("reply_context.html"),
            expected_post_id="ReplyPost1",
            expected_username="reply_user",
            permalink="https://www.threads.com/@reply_user/post/ReplyPost1",
        )
        self.assertEqual(result.status, ExtractionStatus.SUCCESS)
        self.assertEqual(result.text, "Own reply text")

    def test_unrecognized_reply_context_remains_ambiguous(self) -> None:
        result = parse_post_card_html(
            load_html("reply_context.html").replace("Replying to", "Unrecognized context"),
            expected_post_id="ReplyPost1",
            expected_username="reply_user",
            permalink="https://www.threads.com/@reply_user/post/ReplyPost1",
        )
        self.assertEqual(result.status, ExtractionStatus.UNKNOWN_STRUCTURE)
        self.assertEqual(result.reason, "multiple_body_regions_include_ambiguous_reply_context")
        self.assertIsNone(result.text)

    def test_quote_without_own_caption_is_confirmed_no_text(self) -> None:
        html = load_html("quoted_post.html").replace('<div dir="auto">Свой текст ✨</div>', "")
        result = parse_post_card_html(
            html,
            expected_post_id="MainPost1",
            expected_username="main_user",
            permalink="https://www.threads.com/@main_user/post/MainPost1",
        )
        self.assertEqual(result.status, ExtractionStatus.NO_TEXT)
        self.assertEqual(result.text, "")

    def test_quote_without_a_separate_card_boundary_is_unknown(self) -> None:
        html = load_html("quoted_post.html").replace(
            '<article data-pressable-container="true">\n    <div class="quoted-header">',
            '<div class="quoted-card">\n    <div class="quoted-header">',
        ).replace(
            '  </article>\n  <button>Like</button>',
            '  </div>\n  <button>Like</button>',
        )
        result = parse_post_card_html(
            html,
            expected_post_id="MainPost1",
            expected_username="main_user",
            permalink="https://www.threads.com/@main_user/post/MainPost1",
        )
        self.assertEqual(result.status, ExtractionStatus.UNKNOWN_STRUCTURE)
        self.assertEqual(result.reason, "nested_post_boundary_not_found")

    def test_search_recommendation_is_not_evidence_of_target_having_no_text(self) -> None:
        html = load_html("column_with_recommendation.html").replace(
            '<div dir="auto">Авторский текст</div>', ""
        )
        result = parse_post_card_html(
            html,
            expected_post_id="TargetWithRecommendation",
            expected_username="target_user",
            permalink="https://www.threads.com/@target_user/post/TargetWithRecommendation",
        )
        self.assertEqual(result.status, ExtractionStatus.UNKNOWN_STRUCTURE)
        self.assertEqual(result.reason, "own_body_absence_not_confirmed_by_attachment_structure")

    def test_target_text_excludes_recommendation_inside_generic_page_column(self) -> None:
        result = parse_fixture(
            "column_with_recommendation.html",
            "TargetWithRecommendation",
            "target_user",
        )
        self.assertEqual((result.status, result.text), (ExtractionStatus.SUCCESS, "Авторский текст"))
        self.assertNotIn("Текст рекомендации", result.text or "")

    def test_external_link_text_survives_but_preview_is_excluded(self) -> None:
        result = parse_fixture("link_preview.html", "LinkPreview1", "link_user")
        self.assertEqual((result.status, result.text), (ExtractionStatus.SUCCESS, "Ссылка: важная ссылка"))

    def test_browser_rendered_paragraphs_and_link_labels_are_preserved(self) -> None:
        result = parse_fixture("paragraphs.html", "ParagraphPost1", "paragraph_user")
        self.assertEqual(
            result.text,
            "Первый абзац\n\nВторой абзац: важная ссылка 🧵",
        )

    def test_inline_threads_permalink_text_is_not_mistaken_for_a_quote_card(self) -> None:
        result = parse_fixture("threads_inline_link.html", "InlineLink1", "inline_user")
        self.assertEqual(result.status, ExtractionStatus.SUCCESS)
        self.assertEqual(result.text, "Контекст и ссылка на публикацию продолжаются.")

    def test_media_only_post_has_explicit_no_text_status(self) -> None:
        result = parse_fixture("media_only.html", "MediaOnly1", "media_user")
        self.assertEqual(result.status, ExtractionStatus.NO_TEXT)
        self.assertEqual(result.text, "")
        self.assertEqual(result.reason, "identity_verified_attachment_present_without_own_text")

    def test_unloaded_media_is_not_confirmed_as_no_text(self) -> None:
        html = load_html("media_only.html").replace('data-threads-media-ready="true"', 'data-threads-media-ready="false"')
        result = parse_post_card_html(
            html,
            expected_post_id="MediaOnly1",
            expected_username="media_user",
            permalink="https://www.threads.com/@media_user/post/MediaOnly1",
        )
        self.assertEqual(result.status, ExtractionStatus.UNKNOWN_STRUCTURE)

    def test_delayed_body_is_not_treated_as_no_text(self) -> None:
        before = parse_fixture("delayed_before.html", "DelayedPost1", "delayed_user")
        after = parse_fixture("delayed_after.html", "DelayedPost1", "delayed_user")
        self.assertEqual(before.status, ExtractionStatus.UNKNOWN_STRUCTURE)
        self.assertEqual(after.status, ExtractionStatus.SUCCESS)
        self.assertEqual(after.text, "✨ Позже загруженный текст")

    def test_schema_change_returns_unknown_structure(self) -> None:
        result = parse_fixture("unknown_schema.html", "SchemaChange1", "schema_user")
        self.assertEqual(result.status, ExtractionStatus.UNKNOWN_STRUCTURE)
        self.assertEqual(result.reason, "post_card_boundary_missing_or_ambiguous")

    def test_wrong_post_id_and_author_fail_closed(self) -> None:
        wrong_id = parse_fixture("control_a.html", "DifferentPost1", "author_a")
        wrong_author = parse_fixture("control_a.html", "DcljXqxgCut", "other_author")
        self.assertEqual(wrong_id.status, ExtractionStatus.UNKNOWN_STRUCTURE)
        self.assertEqual(wrong_author.status, ExtractionStatus.UNKNOWN_STRUCTURE)

    def test_api_object_requires_author_permalink_and_text(self) -> None:
        item = json.loads((FIXTURES / "api_post.json").read_text(encoding="utf-8"))
        result = parse_api_post(item)
        self.assertEqual(result.status, ExtractionStatus.SUCCESS)
        self.assertEqual(result.post_id, "api-object-123")
        self.assertEqual(result.text, "Один\nдва 🧵")
        self.assertEqual(result.likes_count, 4)

        missing_text = parse_api_post({key: value for key, value in item.items() if key != "text"})
        self.assertEqual(missing_text.status, ExtractionStatus.INCOMPLETE)
        explicit_empty = parse_api_post({**item, "text": ""})
        self.assertEqual(explicit_empty.status, ExtractionStatus.NO_TEXT)

    def test_api_author_mismatch_and_unexpected_text_type_fail_closed(self) -> None:
        item = json.loads((FIXTURES / "api_post.json").read_text(encoding="utf-8"))
        mismatch = parse_api_post({**item, "username": "different_user"})
        bad_text = parse_api_post({**item, "text": ["unexpected"]})
        self.assertEqual(mismatch.status, ExtractionStatus.UNKNOWN_STRUCTURE)
        self.assertEqual(bad_text.status, ExtractionStatus.UNKNOWN_STRUCTURE)

    def test_failed_retry_does_not_replace_confirmed_diagnostic(self) -> None:
        confirmed = PostExtraction(
            post_id="Target1",
            username="author",
            permalink="https://www.threads.com/@author/post/Target1",
            text="confirmed",
            status=ExtractionStatus.SUCCESS,
            source="threads_post_dom",
            parser_version="v2",
            reason="body_verified",
        )
        raw_data = {
            "extraction": confirmed.diagnostic(),
            "text": "old unverified response text",
            "access_token": "must-not-survive",
        }
        failed = PostExtraction(
            post_id="Target1",
            username="author",
            permalink=confirmed.permalink,
            text=None,
            status=ExtractionStatus.UNKNOWN_STRUCTURE,
            source="threads_post_dom",
            parser_version="v3",
            reason="schema_changed",
        )
        updated = add_extraction_diagnostic(raw_data, failed)
        self.assertEqual(extraction_diagnostic(updated, "Target1")["status"], "success")
        self.assertEqual(updated["extraction_last_attempt"]["reason"], "schema_changed")
        self.assertNotIn("text", updated)
        self.assertNotIn("access_token", updated)

    def test_existing_row_is_confirmed_only_for_its_id_and_current_parser(self) -> None:
        diagnostic = {
            "extraction": {
                "post_id": "Target1",
                "source": "threads_post_dom",
                "parser_version": "threads-post-dom-v4",
                "status": "success",
                "reason": "body_verified",
            }
        }
        self.assertTrue(extraction_is_currently_confirmed(diagnostic, "Target1"))
        old_search_card_diagnostic = {
            "extraction": {
                "post_id": "Target2",
                "source": "threads_search_semantic_card_dom",
                "parser_version": "threads-search-semantic-card-v1",
                "status": "success",
            }
        }
        self.assertFalse(extraction_is_currently_confirmed(old_search_card_diagnostic, "Target2"))
        self.assertFalse(extraction_is_currently_confirmed(diagnostic, "Other1"))
        self.assertFalse(
            extraction_is_currently_confirmed(
                {"extraction": {**diagnostic["extraction"], "parser_version": "older-parser"}},
                "Target1",
            )
        )


class ThreadsDiscoveryTests(unittest.TestCase):
    def test_virtualized_batches_keep_ids_that_left_the_dom(self) -> None:
        found: dict[str, DiscoveredPost] = {}
        first_batch = [
            {"id": "PostA", "username": "author", "permalink": "https://www.threads.com/@author/post/PostA"},
            {"id": "PostB", "username": "author", "permalink": "https://www.threads.com/@author/post/PostB"},
        ]
        second_batch = [
            {"id": "PostB", "username": "author", "permalink": "https://www.threads.com/@author/post/PostB"},
            {"id": "PostC", "username": "author", "permalink": "https://www.threads.com/@author/post/PostC"},
        ]
        self.assertEqual(merge_discovery_batch(found, first_batch), 2)
        self.assertEqual(merge_discovery_batch(found, second_batch), 1)
        self.assertEqual(set(found), {"PostA", "PostB", "PostC"})

    def test_conflicting_discovered_identity_is_an_error(self) -> None:
        found: dict[str, DiscoveredPost] = {}
        merge_discovery_batch(found, [
            {"id": "PostA", "username": "author", "permalink": "https://www.threads.com/@author/post/PostA"},
        ])
        with self.assertRaisesRegex(ValueError, "conflicting_search_identity"):
            merge_discovery_batch(found, [
                {"id": "PostA", "username": "impostor", "permalink": "https://www.threads.com/@impostor/post/PostA"},
            ])

    def test_pagination_stops_only_after_unchanged_rendered_state(self) -> None:
        stable = pagination_round_is_stable(
            previous_signature="a|b",
            previous_height=2400,
            next_signature="a|b",
            next_height=2400,
            new_unique_ids=0,
        )
        delayed_batch = pagination_round_is_stable(
            previous_signature="a|b",
            previous_height=2400,
            next_signature="b|c",
            next_height=2400,
            new_unique_ids=1,
        )
        self.assertTrue(stable)
        self.assertFalse(delayed_batch)



if __name__ == "__main__":
    unittest.main()
