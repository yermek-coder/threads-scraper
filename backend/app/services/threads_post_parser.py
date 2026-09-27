"""Identity-first parsing and diagnostics for Threads post content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from html.parser import HTMLParser
import json
from typing import Any, Callable, Iterator
from urllib.parse import unquote, urljoin, urlparse

PARSER_VERSION = "threads-post-dom-v2"
API_PARSER_VERSION = "threads-graph-api-v1"
POST_DOM_SOURCE = "threads_post_dom"
API_SOURCE = "threads_graph_api"
THREADS_HOSTS = frozenset({"threads.com", "www.threads.com", "threads.net", "www.threads.net"})


class ExtractionStatus(StrEnum):
    SUCCESS = "success"
    NO_TEXT = "no_text"
    INCOMPLETE = "incomplete"
    UNAVAILABLE = "unavailable"
    UNKNOWN_STRUCTURE = "unknown_structure"
    LEGACY_UNVERIFIED = "legacy_unverified"


CONFIRMED_STATUSES = frozenset({ExtractionStatus.SUCCESS.value, ExtractionStatus.NO_TEXT.value})
RETRYABLE_STATUSES = frozenset({ExtractionStatus.INCOMPLETE.value, ExtractionStatus.UNAVAILABLE.value})


@dataclass(slots=True)
class PostExtraction:
    """A content result whose identity and extraction state are explicit."""

    post_id: str
    username: str
    permalink: str
    text: str | None
    status: ExtractionStatus
    source: str
    parser_version: str
    reason: str | None = None
    timestamp: str | None = None
    likes_count: int = 0
    replies_count: int = 0
    raw_record: dict[str, Any] | None = None

    @property
    def confirmed(self) -> bool:
        return self.status.value in CONFIRMED_STATUSES

    def diagnostic(self, attempted_at: str | None = None) -> dict[str, Any]:
        """Return safe metadata without post text, cookies, or request headers."""
        return {
            "post_id": self.post_id,
            "source": self.source,
            "parser_version": self.parser_version,
            "status": self.status.value,
            "reason": self.reason,
            "attempted_at": attempted_at or datetime.now(timezone.utc).isoformat(),
        }


@dataclass(slots=True)
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: _Node | None
    children: list[_Node | str]

    def descendants(self, include_self: bool = False) -> Iterator[_Node]:
        if include_self:
            yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield child
                yield from child.descendants()

    def ancestors(self, include_self: bool = False) -> Iterator[_Node]:
        node: _Node | None = self if include_self else self.parent
        while node is not None:
            yield node
            node = node.parent

    def contains(self, other: _Node) -> bool:
        return any(node is other for node in self.descendants(include_self=True))

    def has_ancestor(self, predicate: Callable[[_Node], bool]) -> bool:
        return any(predicate(node) for node in self.ancestors(include_self=True))


class _TreeParser(HTMLParser):
    _VOID_TAGS = frozenset(
        {
            "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
            "param", "source", "track", "wbr",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {}, None, [])
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.lower(), {key.lower(): value or "" for key, value in attrs}, self.stack[-1], [])
        self.stack[-1].children.append(node)
        if tag.lower() not in self._VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.lower(), {key.lower(): value or "" for key, value in attrs}, self.stack[-1], [])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == normalized:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


def _absolute_path(href: str) -> tuple[str, list[str]]:
    parsed = urlparse(urljoin("https://www.threads.com/", href))
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    return parsed.netloc.lower(), parts


def _post_link_identity(node: _Node) -> tuple[str, str] | None:
    if node.tag != "a":
        return None
    host, parts = _absolute_path(node.attrs.get("href", ""))
    if host not in THREADS_HOSTS or len(parts) != 3 or not parts[0].startswith("@") or parts[1] != "post":
        return None
    if not parts[2]:
        return None
    return parts[2], parts[0][1:].casefold()


def _profile_username(node: _Node) -> str | None:
    if node.tag != "a":
        return None
    host, parts = _absolute_path(node.attrs.get("href", ""))
    if host not in THREADS_HOSTS or len(parts) != 1 or not parts[0].startswith("@"):
        return None
    return parts[0][1:].casefold()


def _is_card_boundary(node: _Node) -> bool:
    accessible_label = node.attrs.get("aria-label", "").strip().casefold()
    return (
        node.tag == "article"
        or node.attrs.get("role", "").casefold() == "article"
        or node.attrs.get("data-pressable-container", "").casefold() == "true"
        # The authorized direct-post page exposes this named column in its AX tree.
        or (node.tag in {"main", "section", "div"} and accessible_label == "column body")
    )


def _is_content_card_root(node: _Node) -> bool:
    """True only for an actual post-card boundary, not the whole page column."""
    return (
        node.tag == "article"
        or node.attrs.get("role", "").casefold() == "article"
        or node.attrs.get("data-pressable-container", "").casefold() == "true"
    )


def _lowest_common_ancestor(left: _Node, right: _Node) -> _Node | None:
    right_ancestors = {id(node) for node in right.ancestors(include_self=True)}
    return next((node for node in left.ancestors(include_self=True) if id(node) in right_ancestors), None)


def _distance_to(node: _Node, ancestor: _Node) -> int:
    distance = 0
    current: _Node | None = node
    while current is not None and current is not ancestor:
        distance += 1
        current = current.parent
    return distance


def _hidden(node: _Node) -> bool:
    return node.has_ancestor(
        lambda item: "hidden" in item.attrs
        or item.attrs.get("aria-hidden", "").casefold() == "true"
        or "display:none" in item.attrs.get("style", "").replace(" ", "").casefold()
        or "visibility:hidden" in item.attrs.get("style", "").replace(" ", "").casefold()
    )


def _in_control(node: _Node) -> bool:
    return node.has_ancestor(
        lambda item: item.tag in {"button", "time"} or item.attrs.get("role", "").casefold() == "button"
    )


def _has_dir_auto_descendant(node: _Node) -> bool:
    return any(child.attrs.get("dir", "").casefold() == "auto" for child in node.descendants())


def _find_header(root: _Node, expected_id: str, expected_username: str) -> tuple[_Node | None, int, int]:
    def belongs_to_outer_post(node: _Node) -> bool:
        return not any(_is_card_boundary(parent) and parent is not root for parent in node.ancestors())

    post_links = [
        node for node in root.descendants(include_self=True)
        if _post_link_identity(node) == (expected_id, expected_username.casefold())
        and belongs_to_outer_post(node)
    ]
    author_links = [
        node for node in root.descendants(include_self=True)
        if _profile_username(node) == expected_username.casefold()
        and belongs_to_outer_post(node)
    ]
    pairs: list[tuple[int, _Node]] = []
    for post_link in post_links:
        for author_link in author_links:
            header = _lowest_common_ancestor(post_link, author_link)
            if header is None or header is root:
                continue
            if any(_is_card_boundary(node) and node is not root for node in header.ancestors(include_self=True)):
                continue
            distance = _distance_to(post_link, header) + _distance_to(author_link, header)
            pairs.append((distance, header))
    if not pairs:
        return None, len(post_links), len(author_links)
    best_distance = min(distance for distance, _header in pairs)
    best_headers = {id(header): header for distance, header in pairs if distance == best_distance}
    if len(best_headers) != 1:
        return None, len(post_links), len(author_links)
    return next(iter(best_headers.values()), None), len(post_links), len(author_links)


def _inside_exclusions(node: _Node, excluded_roots: set[int]) -> bool:
    return node.has_ancestor(lambda ancestor: id(ancestor) in excluded_roots)


def _render_text(node: _Node, excluded_roots: set[int]) -> str:
    """Reconstruct text in DOM order while using only explicit block boundaries."""
    block_tags = {"div", "p", "li", "ul", "ol", "section", "blockquote", "pre"}

    def walk(item: _Node | str) -> str:
        if isinstance(item, str):
            return item
        if id(item) in excluded_roots or _hidden(item) or _in_control(item):
            return ""
        if "data-threads-inner-text" in item.attrs:
            # The authenticated browser snapshot annotates structural text
            # regions with their native innerText to preserve rendered breaks.
            return item.attrs["data-threads-inner-text"]
        if item.tag == "br":
            return "\n"
        pieces: list[str] = []
        previous_was_block = False
        last_character = ""
        for child in item.children:
            content = walk(child)
            if content == "":
                continue
            child_is_block = isinstance(child, _Node) and child.tag in block_tags
            if pieces and (previous_was_block or child_is_block) and last_character != "\n":
                pieces.append("\n")
            pieces.append(content)
            last_character = content[-1]
            previous_was_block = child_is_block
        return "".join(pieces)

    return walk(node)


def _embedded_quote_roots(root: _Node, expected_id: str, post_header: _Node) -> tuple[set[int], bool]:
    quote_roots: dict[int, _Node] = {}
    for anchor in root.descendants(include_self=True):
        identity = _post_link_identity(anchor)
        if identity is None or identity[0] == expected_id:
            continue
        boundary = next((node for node in anchor.ancestors(include_self=True) if _is_card_boundary(node)), None)
        if boundary is None or boundary is root:
            # A Threads permalink embedded inside a dir=auto body region is an
            # inline text link. Without that body relationship, its role is
            # ambiguous and the parser must not absorb surrounding page text.
            body_region = next(
                (node for node in anchor.ancestors() if node.attrs.get("dir", "").casefold() == "auto"),
                None,
            )
            if body_region is not None:
                quoted_author_present = any(
                    _profile_username(node) == identity[1]
                    for node in body_region.descendants(include_self=True)
                )
                if quoted_author_present:
                    return set(), False
                for author_link in root.descendants():
                    if _profile_username(author_link) != identity[1] or post_header.contains(author_link):
                        continue
                    shared = _lowest_common_ancestor(anchor, author_link)
                    if (
                        shared is not None
                        and shared is not root
                        and shared is not body_region
                        and not body_region.contains(shared)
                        and not post_header.contains(shared)
                    ):
                        return set(), False
                continue
            return set(), False
        quote_roots[id(boundary)] = boundary
    return set(quote_roots), True


def _link_preview_roots(root: _Node) -> set[int]:
    previews: set[int] = set()
    for anchor in (node for node in root.descendants(include_self=True) if node.tag == "a"):
        host, _parts = _absolute_path(anchor.attrs.get("href", ""))
        if host in THREADS_HOSTS:
            continue
        if any(node.tag in {"img", "video", "iframe"} for node in anchor.descendants()):
            previews.add(id(anchor))
    return previews


def _media_ready(node: _Node) -> bool:
    return node.attrs.get("data-threads-media-ready", "").casefold() == "true"


def _has_media_or_quote(
    root: _Node,
    header: _Node,
    excluded_roots: set[int],
    quote_roots: set[int],
) -> bool:
    # A generic page column can contain media from replies or recommendations.
    # Without a target-card boundary those attachments cannot prove that the
    # target post itself has no text.
    if not _is_content_card_root(root):
        return False
    for node in root.descendants():
        if node.tag not in {"img", "video", "iframe"} or header.contains(node) or _hidden(node):
            continue
        if not _media_ready(node):
            continue
        if _inside_exclusions(node, excluded_roots):
            continue
        return True
    preview_roots = _link_preview_roots(root)
    ready_previews = any(
        id(anchor) in preview_roots
        and not _inside_exclusions(anchor, excluded_roots)
        and any(_media_ready(media) for media in anchor.descendants() if media.tag in {"img", "video", "iframe"})
        for anchor in root.descendants()
        if anchor.tag == "a"
    )
    return bool(quote_roots or ready_previews)


def _record(
    *,
    post_id: str,
    username: str,
    permalink: str,
    status: ExtractionStatus,
    reason: str,
    text: str | None = None,
    timestamp: str | None = None,
) -> PostExtraction:
    return PostExtraction(
        post_id=post_id,
        username=username,
        permalink=permalink,
        text=text,
        status=status,
        source=POST_DOM_SOURCE,
        parser_version=PARSER_VERSION,
        reason=reason,
        timestamp=timestamp,
    )


def parse_post_card_html(
    card_html: str,
    *,
    expected_post_id: str,
    expected_username: str,
    permalink: str,
    timestamp: str | None = None,
) -> PostExtraction:
    """Parse only a structurally bounded card for a verified post permalink."""
    parser = _TreeParser()
    parser.feed(card_html)
    boundaries = [node for node in parser.root.descendants() if _is_card_boundary(node)]
    outer_boundaries = [
        node for node in boundaries
        if not any(_is_card_boundary(parent) for parent in node.ancestors())
    ]
    if len(outer_boundaries) != 1:
        return _record(
            post_id=expected_post_id,
            username=expected_username,
            permalink=permalink,
            status=ExtractionStatus.UNKNOWN_STRUCTURE,
            reason="post_card_boundary_missing_or_ambiguous",
            timestamp=timestamp,
        )
    root = outer_boundaries[0]
    header, post_link_count, author_link_count = _find_header(root, expected_post_id, expected_username)
    if post_link_count == 0:
        reason = "post_id_not_present_in_card_permalink"
    elif author_link_count == 0:
        reason = "post_author_not_present_in_card"
    elif header is None:
        reason = "post_header_relationship_is_ambiguous"
    else:
        reason = ""
    if reason:
        return _record(
            post_id=expected_post_id,
            username=expected_username,
            permalink=permalink,
            status=ExtractionStatus.UNKNOWN_STRUCTURE,
            reason=reason,
            timestamp=timestamp,
        )

    assert header is not None
    quote_roots, quotes_are_separable = _embedded_quote_roots(root, expected_post_id, header)
    if not quotes_are_separable:
        return _record(
            post_id=expected_post_id,
            username=expected_username,
            permalink=permalink,
            status=ExtractionStatus.UNKNOWN_STRUCTURE,
            reason="nested_post_boundary_not_found",
            timestamp=timestamp,
        )
    preview_roots = _link_preview_roots(root)
    excluded_roots = quote_roots | preview_roots
    body_regions = [
        node for node in root.descendants()
        if node.attrs.get("dir", "").casefold() == "auto"
        and not _has_dir_auto_descendant(node)
        and not header.contains(node)
        and not _in_control(node)
        and not _hidden(node)
        and not _inside_exclusions(node, excluded_roots)
    ]
    if len(body_regions) > 1 and any(
        any(descendant.attrs.get("translate", "").casefold() == "no" for descendant in region.descendants())
        for region in body_regions
    ):
        return _record(
            post_id=expected_post_id,
            username=expected_username,
            permalink=permalink,
            status=ExtractionStatus.UNKNOWN_STRUCTURE,
            reason="multiple_body_regions_include_ambiguous_reply_context",
            timestamp=timestamp,
        )
    text_fragments = [_render_text(node, excluded_roots) for node in body_regions]
    text = "\n".join(fragment for fragment in text_fragments if fragment != "")
    if text != "":
        return _record(
            post_id=expected_post_id,
            username=expected_username,
            permalink=permalink,
            status=ExtractionStatus.SUCCESS,
            reason="post_id_author_header_and_body_regions_verified",
            text=text,
            timestamp=timestamp,
        )
    if body_regions:
        return _record(
            post_id=expected_post_id,
            username=expected_username,
            permalink=permalink,
            status=ExtractionStatus.UNKNOWN_STRUCTURE,
            reason="body_region_is_present_but_empty",
            timestamp=timestamp,
        )
    if _has_media_or_quote(root, header, excluded_roots, quote_roots):
        return _record(
            post_id=expected_post_id,
            username=expected_username,
            permalink=permalink,
            status=ExtractionStatus.NO_TEXT,
            reason="identity_verified_attachment_present_without_own_text",
            text="",
            timestamp=timestamp,
        )
    return _record(
        post_id=expected_post_id,
        username=expected_username,
        permalink=permalink,
        status=ExtractionStatus.UNKNOWN_STRUCTURE,
        reason="own_body_absence_not_confirmed_by_attachment_structure",
        timestamp=timestamp,
    )


def parse_api_post(item: dict[str, Any]) -> PostExtraction:
    """Validate the same-object ID, author, permalink, and text fields from API data."""
    post_id = item.get("id")
    username = item.get("username")
    permalink = item.get("permalink")
    normalized_id = str(post_id) if isinstance(post_id, (str, int)) else ""
    normalized_permalink = permalink if isinstance(permalink, str) else ""
    if not normalized_id:
        reason = "id_field_missing_or_invalid"
        normalized_username = username if isinstance(username, str) else ""
        status = ExtractionStatus.UNKNOWN_STRUCTURE
    elif not isinstance(username, str) or not username:
        reason = "username_field_missing_or_invalid"
        normalized_username = ""
        status = ExtractionStatus.UNKNOWN_STRUCTURE
    elif not isinstance(permalink, str) or not permalink:
        reason = "permalink_field_missing_or_invalid"
        normalized_username = username
        status = ExtractionStatus.UNKNOWN_STRUCTURE
    else:
        host, parts = _absolute_path(permalink)
        normalized_username = username
        if host not in THREADS_HOSTS or len(parts) != 3 or parts[0].casefold() != f"@{username}".casefold() or parts[1] != "post":
            reason = "api_author_and_permalink_do_not_match"
            status = ExtractionStatus.UNKNOWN_STRUCTURE
        elif "text" not in item:
            reason = "text_field_missing_from_api_object"
            status = ExtractionStatus.INCOMPLETE
        elif not isinstance(item.get("text"), str):
            reason = "text_field_has_unexpected_type"
            status = ExtractionStatus.UNKNOWN_STRUCTURE
        else:
            text = item["text"]
            return PostExtraction(
                post_id=normalized_id,
                username=username,
                permalink=permalink,
                text=text,
                status=ExtractionStatus.NO_TEXT if text == "" else ExtractionStatus.SUCCESS,
                source=API_SOURCE,
                parser_version=API_PARSER_VERSION,
                reason="same_api_object_id_author_permalink_and_text_verified" if text else "explicit_empty_text_field",
                timestamp=item.get("timestamp") if isinstance(item.get("timestamp"), str) else None,
                likes_count=_nonnegative_int(item.get("like_count", item.get("likes_count", 0))),
                replies_count=_nonnegative_int(item.get("reply_count", item.get("replies_count", 0))),
            )
    return PostExtraction(
        post_id=normalized_id,
        username=normalized_username,
        permalink=normalized_permalink,
        text=None,
        status=status,
        source=API_SOURCE,
        parser_version=API_PARSER_VERSION,
        reason=reason,
    )


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def extraction_diagnostic(raw_data: dict[str, Any] | None, post_id: str) -> dict[str, Any]:
    """Return verified metadata or mark a pre-parser row as legacy/unverified."""
    raw_data = raw_data if isinstance(raw_data, dict) else {}
    extraction = raw_data.get("extraction")
    if (
        isinstance(extraction, dict)
        and str(extraction.get("post_id") or "") == post_id
        and isinstance(extraction.get("status"), str)
    ):
        return extraction
    return {
        "post_id": post_id,
        "source": raw_data.get("source") if isinstance(raw_data.get("source"), str) else "unknown",
        "parser_version": "legacy",
        "status": ExtractionStatus.LEGACY_UNVERIFIED.value,
        "reason": "row_has_no_identity_checked_extraction_metadata",
        "attempted_at": None,
    }


def extraction_is_currently_confirmed(raw_data: dict[str, Any] | None, post_id: str) -> bool:
    """Confirm an existing row only when its ID, status, source, and parser version match."""
    diagnostic = extraction_diagnostic(raw_data, post_id)
    parser_versions = {
        POST_DOM_SOURCE: PARSER_VERSION,
        API_SOURCE: API_PARSER_VERSION,
        "threads_webhook": "threads-webhook-v1",
    }
    source = diagnostic.get("source")
    return (
        diagnostic.get("status") in CONFIRMED_STATUSES
        and str(diagnostic.get("post_id") or "") == post_id
        and isinstance(source, str)
        and parser_versions.get(source) == diagnostic.get("parser_version")
    )


def add_extraction_diagnostic(raw_data: dict[str, Any] | None, extraction: PostExtraction) -> dict[str, Any]:
    """Write safe extraction metadata and preserve confirmed state on failed retries."""
    current = extraction_diagnostic(raw_data, extraction.post_id)
    attempted = extraction.diagnostic()
    if extraction.confirmed or current.get("status") not in CONFIRMED_STATUSES:
        return {"source": extraction.source, "extraction": attempted}
    previous = {
        key: current[key]
        for key in ("post_id", "source", "parser_version", "status", "reason", "attempted_at")
        if key in current
    }
    return {
        "source": current.get("source") if isinstance(current.get("source"), str) else extraction.source,
        "extraction": previous,
        "extraction_last_attempt": attempted,
    }


def parser_shape_fingerprint(card_html: str) -> str:
    """Describe only tag/attribute shape for safe unknown-schema diagnostics."""
    parser = _TreeParser()
    parser.feed(card_html)
    shape = [
        (node.tag, sorted(key for key in node.attrs if key in {"role", "dir", "data-pressable-container", "aria-label"}))
        for node in parser.root.descendants()
    ]
    return json.dumps(shape, ensure_ascii=True, separators=(",", ":"))
