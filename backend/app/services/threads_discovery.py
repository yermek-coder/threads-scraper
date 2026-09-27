"""Pure identity and pagination helpers for virtualized Threads search results."""

from __future__ import annotations

from dataclasses import dataclass, replace
from urllib.parse import unquote, urlparse

THREADS_HOSTS = frozenset({"threads.com", "www.threads.com", "threads.net", "www.threads.net"})


@dataclass(slots=True, frozen=True)
class DiscoveredPost:
    """Candidate discovered from a search permalink and optional engagement counts."""

    post_id: str
    username: str
    permalink: str
    likes_count: int | None = None
    replies_count: int | None = None
    reposts_count: int | None = None


def _optional_count(item: dict[str, object], field: str) -> int | None:
    """Validate a count when the discovery script supplied one."""
    if field not in item:
        return None
    value = item[field]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"search_candidate_{field}_invalid")
    return value


def _highest_known_count(previous: int | None, current: int | None) -> int | None:
    """Keep the largest observed count, including when a later card omits it."""
    if previous is None:
        return current
    if current is None:
        return previous
    return max(previous, current)


def _validated_candidate(item: dict[str, object]) -> DiscoveredPost:
    post_id = item.get("id")
    username = item.get("username")
    permalink = item.get("permalink")
    if not all(isinstance(value, str) and value for value in (post_id, username, permalink)):
        raise ValueError("search_candidate_identity_fields_invalid")
    assert isinstance(post_id, str) and isinstance(username, str) and isinstance(permalink, str)
    parsed = urlparse(permalink)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if (
        parsed.hostname not in THREADS_HOSTS
        or len(parts) != 3
        or not parts[0].startswith("@")
        or parts[0][1:].casefold() != username.casefold()
        or parts[1] != "post"
        or parts[2] != post_id
    ):
        raise ValueError("search_candidate_permalink_identity_mismatch")
    return DiscoveredPost(
        post_id=post_id,
        username=username,
        permalink=permalink,
        likes_count=_optional_count(item, "likes_count"),
        replies_count=_optional_count(item, "replies_count"),
        reposts_count=_optional_count(item, "reposts_count"),
    )


def merge_discovery_batch(found: dict[str, DiscoveredPost], items: object) -> int:
    """Merge one rendered batch so virtualized-away IDs remain discoverable."""
    if not isinstance(items, list):
        raise ValueError("search_candidate_batch_invalid")
    before = len(found)
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("search_candidate_record_invalid")
        candidate = _validated_candidate(item)
        prior = found.get(candidate.post_id)
        if prior is None:
            found[candidate.post_id] = candidate
        elif (prior.username.casefold(), urlparse(prior.permalink).path) != (
            candidate.username.casefold(),
            urlparse(candidate.permalink).path,
        ):
            raise ValueError("conflicting_search_identity_for_post_id")
        else:
            found[candidate.post_id] = replace(
                prior,
                likes_count=_highest_known_count(prior.likes_count, candidate.likes_count),
                replies_count=_highest_known_count(prior.replies_count, candidate.replies_count),
                reposts_count=_highest_known_count(prior.reposts_count, candidate.reposts_count),
            )
    return len(found) - before


def pagination_round_is_stable(
    *,
    previous_signature: str,
    previous_height: int,
    next_signature: str,
    next_height: int,
    new_unique_ids: int,
) -> bool:
    """A round is stable only when the DOM snapshot and unique-ID set stop changing."""
    return new_unique_ids == 0 and previous_signature == next_signature and previous_height == next_height
