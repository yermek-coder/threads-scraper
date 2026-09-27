"""Pure identity and pagination helpers for virtualized Threads search results."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlparse

THREADS_HOSTS = frozenset({"threads.com", "www.threads.com", "threads.net", "www.threads.net"})


@dataclass(slots=True, frozen=True)
class DiscoveredPost:
    """Identity-only candidate discovered from a search permalink."""

    post_id: str
    username: str
    permalink: str


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
    return DiscoveredPost(post_id=post_id, username=username, permalink=permalink)


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
