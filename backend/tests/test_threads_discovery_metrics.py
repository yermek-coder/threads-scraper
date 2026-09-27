from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.threads_discovery import DiscoveredPost, merge_discovery_batch


def candidate(post_id: str, username: str = "author", **counts: object) -> dict[str, object]:
    """Build a discovery record with the same keys returned by browser JavaScript."""
    return {
        "id": post_id,
        "username": username,
        "permalink": f"https://www.threads.com/@{username}/post/{post_id}",
        **counts,
    }


class ThreadsDiscoveryMetricsTests(unittest.TestCase):
    def test_virtualized_batches_keep_previous_posts_and_add_later_counts(self) -> None:
        found: dict[str, DiscoveredPost] = {}

        self.assertEqual(
            merge_discovery_batch(
                found,
                [candidate("PostA", likes_count=4), candidate("PostB")],
            ),
            2,
        )
        self.assertEqual(
            merge_discovery_batch(
                found,
                [
                    candidate("PostB", replies_count=2, reposts_count=0),
                    candidate("PostC", likes_count=7),
                ],
            ),
            1,
        )

        self.assertEqual(set(found), {"PostA", "PostB", "PostC"})
        self.assertEqual(found["PostA"].likes_count, 4)
        self.assertEqual(
            (found["PostB"].likes_count, found["PostB"].replies_count, found["PostB"].reposts_count),
            (None, 2, 0),
        )
        self.assertEqual(found["PostC"].likes_count, 7)

    def test_duplicate_observations_increase_counts_and_never_erase_known_values(self) -> None:
        found: dict[str, DiscoveredPost] = {}
        self.assertEqual(
            merge_discovery_batch(
                found,
                [
                    candidate("PostA", likes_count=2, replies_count=1),
                    candidate("PostA", likes_count=8, reposts_count=3),
                    candidate("PostA", likes_count=5, replies_count=0, reposts_count=1),
                ],
            ),
            1,
        )
        self.assertEqual(
            (found["PostA"].likes_count, found["PostA"].replies_count, found["PostA"].reposts_count),
            (8, 1, 3),
        )
        self.assertEqual(merge_discovery_batch(found, [candidate("PostA")]), 0)
        self.assertEqual(
            (found["PostA"].likes_count, found["PostA"].replies_count, found["PostA"].reposts_count),
            (8, 1, 3),
        )

    def test_identity_conflict_is_rejected_before_counts_are_updated(self) -> None:
        found: dict[str, DiscoveredPost] = {}
        merge_discovery_batch(found, [candidate("PostA", likes_count=2)])

        with self.assertRaisesRegex(ValueError, "conflicting_search_identity_for_post_id"):
            merge_discovery_batch(found, [candidate("PostA", username="impostor", likes_count=99)])

        self.assertEqual(found["PostA"].username, "author")
        self.assertEqual(found["PostA"].likes_count, 2)

    def test_invalid_present_counts_are_rejected(self) -> None:
        for field in ("likes_count", "replies_count", "reposts_count"):
            for invalid in (-1, True, False, 1.5, "2", None):
                with self.subTest(field=field, invalid=invalid):
                    found: dict[str, DiscoveredPost] = {}
                    with self.assertRaisesRegex(ValueError, f"search_candidate_{field}_invalid"):
                        merge_discovery_batch(found, [candidate("PostA", **{field: invalid})])
                    self.assertEqual(found, {})

    def test_missing_counts_default_to_unknown_and_zero_is_valid(self) -> None:
        manual = DiscoveredPost("PostA", "author", "https://www.threads.com/@author/post/PostA")
        self.assertEqual((manual.likes_count, manual.replies_count, manual.reposts_count), (None, None, None))

        found: dict[str, DiscoveredPost] = {}
        merge_discovery_batch(found, [candidate("PostA", likes_count=0, replies_count=0, reposts_count=0)])
        self.assertEqual(
            (found["PostA"].likes_count, found["PostA"].replies_count, found["PostA"].reposts_count),
            (0, 0, 0),
        )


if __name__ == "__main__":
    unittest.main()
