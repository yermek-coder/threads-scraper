"""Capture search cards while scrolling; retain direct-page recovery helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any
from urllib.parse import quote_plus, unquote, urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings_service import get_setting
from app.services.threads_discovery import (
    DiscoveredPost,
    merge_discovery_batch,
    pagination_round_is_stable,
)
from app.services.threads_errors import ThreadsCollectionError
from app.services.threads_post_parser import (
    ExtractionStatus,
    PostExtraction,
    POST_DOM_SOURCE,
    PARSER_VERSION,
    SEARCH_DOM_SOURCE,
    SEARCH_PARSER_VERSION,
    parse_post_card_html,
)

logger = logging.getLogger(__name__)
_SESSION_KEY = "threads_web_storage_state"
_SEARCH_URL = "https://www.threads.com/search?q={query}&serp_type=default&filter=recent"
_MAX_SEARCH_SCROLL_ROUNDS = 60
_MAX_DISCOVERED_POSTS = 250
_MAX_POST_FETCHES_PER_RUN = 100
_SEARCH_CHANGE_TIMEOUT_MS = 1_800
_SEARCH_INTERIOR_CHANGE_TIMEOUT_MS = 250
_POST_READY_TIMEOUT_MS = 18_000
_POST_FETCH_ATTEMPTS = 2
_browser_lock = asyncio.Lock()
_playwright_instance: Any = None
_browser_instance: Any = None


@dataclass(slots=True)
class DiscoveryResult:
    posts: list[DiscoveredPost]
    extractions: dict[str, PostExtraction]
    complete: bool
    rounds: int
    reason: str | None = None


_SEARCH_COLUMN_READY_SCRIPT = r"""() =>
  /^\/search\/?$/.test(location.pathname) && document.querySelector('[aria-label="Column body"]') !== null
"""

_SEARCH_DISCOVERY_SCRIPT = r"""() => {
  const column = document.querySelector('[aria-label="Column body"]');
  if (!column) return { structure: false, posts: [] };
  const posts = [];
  const signals = [];
  const supportedHosts = new Set(['threads.com', 'www.threads.com', 'threads.net', 'www.threads.net']);
  const cardSelector = '[data-pressable-container="true"],article,[role="article"]';
  const ownTime = (card) => Array.from(card.querySelectorAll('time[datetime]'))
    .find((node) => node.closest(cardSelector) === card);
  const identity = (href) => {
    try {
      const url = new URL(href, location.href);
      const parts = url.pathname.split('/').filter(Boolean).map((part) => decodeURIComponent(part));
      if (!supportedHosts.has(url.hostname.toLowerCase()) || parts.length !== 3
          || !parts[0].startsWith('@') || parts[1] !== 'post' || !parts[2]) return null;
      return { id: parts[2], username: parts[0].slice(1), permalink: `${url.origin}${url.pathname}` };
    } catch { return null; }
  };
  const readCount = (value) => {
    const text = String(value || '').replace(/\u00a0/g, ' ').trim();
    if (!text) return null;
    const abbreviated = text.match(/^([\d.,]+)\s*(K|M|B|тыс\.?|млн\.?|млрд\.?)$/i);
    if (abbreviated) {
      const amount = Number(abbreviated[1].replace(',', '.'));
      const suffix = abbreviated[2].toLowerCase();
      const factor = suffix.startsWith('k') || suffix.startsWith('тыс') ? 1_000
        : suffix.startsWith('m') || suffix.startsWith('млн') ? 1_000_000 : 1_000_000_000;
      return Number.isFinite(amount) ? Math.round(amount * factor) : null;
    }
    return /^[\d\s,.]+$/.test(text) ? Number(text.replace(/[\s,.]/g, '')) : null;
  };
  const cards = Array.from(column.querySelectorAll(cardSelector)).filter((card) => {
    if (!ownTime(card)) return false;
    for (let parent = card.parentElement?.closest(cardSelector);
         parent && column.contains(parent);
         parent = parent.parentElement?.closest(cardSelector)) {
      if (ownTime(parent)) return false;
    }
    return true;
  });
  for (const card of cards) {
    const time = ownTime(card);
    const postAnchor = time?.closest('a[href]');
    const post = postAnchor ? identity(postAnchor.href) : null;
    if (!post) continue;
    const hasAuthor = Array.from(card.querySelectorAll('a[href]')).some((anchor) => {
      if (anchor.closest(cardSelector) !== card) return false;
      try {
        const url = new URL(anchor.href, location.href);
        const parts = url.pathname.split('/').filter(Boolean).map((part) => decodeURIComponent(part));
        return supportedHosts.has(url.hostname.toLowerCase()) && parts.length === 1
          && parts[0].toLowerCase() === `@${post.username.toLowerCase()}`;
      } catch { return false; }
    });
    if (!hasAuthor) continue;

    // Copy the card before Threads virtualizes it away. The post-card
    // parser checks the permalink/author/header/body relationship on this
    // bounded card; search results never supply trusted body text by position.
    const clone = card.cloneNode(true);
    const originalRegions = Array.from(card.querySelectorAll('[dir="auto"]'));
    const clonedRegions = Array.from(clone.querySelectorAll('[dir="auto"]'));
    const regionContainsExcludedContent = (region) => {
      if (region.querySelector(cardSelector)) return true;
      for (const anchor of region.querySelectorAll('a[href]')) {
        try {
          const url = new URL(anchor.href, location.href);
          if (!supportedHosts.has(url.hostname.toLowerCase()) && anchor.querySelector('img,video,iframe')) return true;
        } catch {}
      }
      return false;
    };
    for (let i = 0; i < originalRegions.length; i += 1) {
      const region = originalRegions[i];
      if (!region.querySelector('[dir="auto"],button,[role="button"],time')
          && !regionContainsExcludedContent(region))
        clonedRegions[i].setAttribute('data-threads-inner-text', region.innerText || '');
    }
    const originalMedia = Array.from(card.querySelectorAll('img,video,iframe'));
    const clonedMedia = Array.from(clone.querySelectorAll('img,video,iframe'));
    for (let i = 0; i < originalMedia.length; i += 1) {
      const media = originalMedia[i];
      const ready = media instanceof HTMLImageElement ? media.complete && media.naturalWidth > 0
        : media instanceof HTMLVideoElement ? media.readyState >= 1 || Boolean(media.poster)
        : media instanceof HTMLIFrameElement && media.contentDocument?.readyState === 'complete';
      clonedMedia[i].setAttribute('data-threads-media-ready', ready ? 'true' : 'false');
    }
    for (const control of card.querySelectorAll('button,[role="button"]')) {
      if (control.closest(cardSelector) !== card) continue;
      const labels = [
        control.querySelector('svg title')?.textContent,
        control.querySelector('svg[aria-label]')?.getAttribute('aria-label'),
        control.getAttribute('aria-label')
      ].filter(Boolean).map((label) => label.trim().toLowerCase());
      const key = labels.map((label) =>
        /^(like|unlike|нравится|не нравится)(?:[\s,:]|$)/i.test(label) ? 'likes_count'
          : /^(comment|reply|комментировать|комментарии|ответить|ответы)(?:[\s,:]|$)/i.test(label) ? 'replies_count'
          : /^(repost|репост|репостнуть|сделать репост)(?:[\s,:]|$)/i.test(label) ? 'reposts_count'
          : null
      ).find(Boolean);
      if (!key) continue;
      const value = readCount(control.innerText);
      if (Number.isSafeInteger(value) && value >= 0) post[key] = value;
    }
    post.html = clone.outerHTML;
    post.timestamp = time.getAttribute('datetime');
    post.is_loading = Boolean(card.querySelector('[aria-busy="true"],[role="progressbar"]'));
    post.is_truncated = Array.from(card.querySelectorAll('button,[role="button"]')).some((control) =>
      control.closest(cardSelector) === card
      && /^(?:(?:\.\.\.|…)\s*)?(see more|show more|read more|more|ещ[её]|показать ещ[её]|читать дальше|подробнее)$/i.test(control.innerText.trim()));
    posts.push(post);
    signals.push(`${postAnchor.getAttribute('href')}:${card.outerHTML.length}`);
  }
  const signature = signals.join('|');
  const scroller = column.scrollHeight > column.clientHeight + 1 ? column : document.scrollingElement;
  return {
    structure: true,
    posts,
    signature,
    is_loading: Boolean(column.querySelector('[aria-busy="true"],[role="progressbar"]')),
    scrollHeight: scroller?.scrollHeight || document.documentElement.scrollHeight,
    scrollTop: scroller?.scrollTop || window.scrollY,
    clientHeight: scroller?.clientHeight || window.innerHeight
  };
}"""

_SEARCH_STATE_CHANGED_SCRIPT = r"""(previous) => {
  const column = document.querySelector('[aria-label="Column body"]');
  if (!column) return true;
  const cardSelector = '[data-pressable-container="true"],article,[role="article"]';
  const ownTime = (card) => Array.from(card.querySelectorAll('time[datetime]'))
    .find((node) => node.closest(cardSelector) === card);
  const signature = Array.from(column.querySelectorAll(cardSelector))
    .filter((card) => {
      if (!ownTime(card)) return false;
      for (let parent = card.parentElement?.closest(cardSelector);
           parent && column.contains(parent);
           parent = parent.parentElement?.closest(cardSelector)) {
        if (ownTime(parent)) return false;
      }
      return true;
    })
    .map((card) => {
      const time = ownTime(card);
      const link = time?.closest('a[href]');
      return link ? `${link.getAttribute('href')}:${card.outerHTML.length}` : '';
    }).filter(Boolean).join('|');
  const isLoading = Boolean(column.querySelector('[aria-busy="true"],[role="progressbar"]'));
  const scroller = column.scrollHeight > column.clientHeight + 1 ? column : document.scrollingElement;
  const height = scroller?.scrollHeight || document.documentElement.scrollHeight;
  return signature !== previous.signature || height !== previous.scrollHeight || isLoading !== previous.isLoading;
}"""

_POST_ROOT_AND_READY_SCRIPT = r"""(expected) => {
  const normalized = (value) => String(value || '').toLowerCase();
  const routeParts = location.pathname.split('/').filter(Boolean).map((part) => decodeURIComponent(part));
  if (routeParts.length !== 3 || normalized(routeParts[0]) !== `@${expected.username.toLowerCase()}`
      || routeParts[1] !== 'post' || routeParts[2] !== expected.postId) return false;
  const samePostLink = (anchor) => {
    if (!(anchor instanceof HTMLAnchorElement)) return false;
    try {
      const url = new URL(anchor.href, location.href);
      const parts = url.pathname.split('/').filter(Boolean).map((part) => decodeURIComponent(part));
      return ['threads.com', 'www.threads.com', 'threads.net', 'www.threads.net'].includes(url.hostname.toLowerCase())
        && parts.length === 3 && normalized(parts[0]) === `@${expected.username.toLowerCase()}`
        && parts[1] === 'post' && parts[2] === expected.postId;
    } catch { return false; }
  };
  const authorLinkMatches = (anchor) => {
    if (!(anchor instanceof HTMLAnchorElement)) return false;
    try {
      const url = new URL(anchor.href, location.href);
      const parts = url.pathname.split('/').filter(Boolean).map((part) => decodeURIComponent(part));
      return ['threads.com', 'www.threads.com', 'threads.net', 'www.threads.net'].includes(url.hostname.toLowerCase())
        && parts.length === 1 && normalized(parts[0]) === `@${expected.username.toLowerCase()}`;
    } catch { return false; }
  };
  const postAnchor = Array.from(document.querySelectorAll('a[href]')).find(samePostLink);
  if (!postAnchor) return false;
  const card = postAnchor.closest('[data-pressable-container="true"],article,[role="article"]')
    || document.querySelector('[aria-label="Column body"]');
  if (!card || !card.contains(postAnchor)) return false;
  const hasAuthor = Array.from(card.querySelectorAll('a[href]')).some(authorLinkMatches);
  if (!hasAuthor) return false;

  const header = (() => {
    const ancestors = new Set();
    for (let node = postAnchor; node; node = node.parentElement) ancestors.add(node);
    const author = Array.from(card.querySelectorAll('a[href]')).find(authorLinkMatches);
    for (let node = author; node; node = node.parentElement) if (ancestors.has(node)) return node;
    return null;
  })();
  if (!header || header === card) return false;
  const nestedPostCard = (element) => {
    const boundary = element.closest('[data-pressable-container="true"],article,[role="article"]');
    return Boolean(boundary && boundary !== card);
  };
  const bodyRegions = Array.from(card.querySelectorAll('[dir="auto"]')).filter((element) => {
    if (element.querySelector('[dir="auto"]') || header.contains(element)) return false;
    if (element.closest('button,[role="button"],time') || nestedPostCard(element)) return false;
    return true;
  });
  const hasBodyText = bodyRegions.some((element) => (element.innerText || '') !== '');
  const hasEmptyBodyRegion = bodyRegions.some((element) => (element.innerText || '') === '');
  const mediaIsReady = (element) => {
    if (element instanceof HTMLImageElement) return element.complete && element.naturalWidth > 0;
    if (element instanceof HTMLVideoElement) return element.readyState >= 1 || Boolean(element.poster);
    if (element instanceof HTMLIFrameElement) return Boolean(element.contentDocument?.readyState === 'complete');
    return false;
  };
  const hasMedia = Array.from(card.querySelectorAll('img,video,iframe')).some((element) => {
    if (header.contains(element) || nestedPostCard(element)) return false;
    return mediaIsReady(element);
  });
  const hasQuotedPost = Array.from(card.querySelectorAll('a[href]')).some((anchor) => {
    if (samePostLink(anchor)) return false;
    try {
      const url = new URL(anchor.href, location.href);
      const parts = url.pathname.split('/').filter(Boolean);
      if (parts.length !== 3 || !parts[0].startsWith('@') || parts[1] !== 'post') return false;
      const boundary = anchor.closest('[data-pressable-container="true"],article,[role="article"]');
      return boundary && boundary !== card;
    } catch { return false; }
  });
  return hasBodyText || ((hasMedia || hasQuotedPost) && !hasEmptyBodyRegion);
}"""

_POST_SNAPSHOT_SCRIPT = r"""(expected) => {
  const username = expected.username.toLowerCase();
  const postId = expected.postId;
  const matchesPost = (anchor) => {
    try {
      const url = new URL(anchor.href, location.href);
      const parts = url.pathname.split('/').filter(Boolean).map((part) => decodeURIComponent(part));
      return ['threads.com', 'www.threads.com', 'threads.net', 'www.threads.net'].includes(url.hostname.toLowerCase())
        && parts.length === 3 && parts[0].toLowerCase() === `@${username}` && parts[1] === 'post' && parts[2] === postId;
    } catch { return false; }
  };
  const postAnchor = Array.from(document.querySelectorAll('a[href]')).find(matchesPost);
  if (!postAnchor) return { reason: 'target_permalink_anchor_missing' };
  const card = postAnchor.closest('[data-pressable-container="true"],article,[role="article"]')
    || document.querySelector('[aria-label="Column body"]');
  if (!card) return { reason: 'target_post_container_missing' };
  const hasAuthor = Array.from(card.querySelectorAll('a[href]')).some((anchor) => {
    try {
      const url = new URL(anchor.href, location.href);
      const parts = url.pathname.split('/').filter(Boolean).map((part) => decodeURIComponent(part));
      return ['threads.com', 'www.threads.com', 'threads.net', 'www.threads.net'].includes(url.hostname.toLowerCase())
        && parts.length === 1 && parts[0].toLowerCase() === `@${username}`;
    } catch { return false; }
  });
  if (!hasAuthor) return { reason: 'target_author_link_missing' };
  const time = card.querySelector('time[datetime]');
  const clone = card.cloneNode(true);
  const textRegions = (root) => [
    ...(root.matches?.('[dir="auto"]') ? [root] : []),
    ...root.querySelectorAll('[dir="auto"]')
  ];
  const originalRegions = textRegions(card);
  const clonedRegions = textRegions(clone);
  if (originalRegions.length !== clonedRegions.length) return { reason: 'text_region_snapshot_mismatch' };
  const regionContainsExcludedContent = (region) => {
    if (region.querySelector('button,[role="button"],time')) return true;
    for (const anchor of region.querySelectorAll('a[href]')) {
      try {
        const url = new URL(anchor.href, location.href);
        const parts = url.pathname.split('/').filter(Boolean).map((part) => decodeURIComponent(part));
        const isExternalPreview = !['threads.com', 'www.threads.com', 'threads.net', 'www.threads.net'].includes(url.hostname.toLowerCase())
          && Boolean(anchor.querySelector('img,video,iframe'));
        if (isExternalPreview) return true;
        const isDifferentPost = parts.length === 3 && parts[0].startsWith('@') && parts[1] === 'post' && parts[2] !== postId;
        const boundary = anchor.closest('[data-pressable-container="true"],article,[role="article"]');
        if (isDifferentPost && boundary && boundary !== card) return true;
      } catch {}
    }
    return false;
  };
  for (let index = 0; index < originalRegions.length; index += 1) {
    if (!regionContainsExcludedContent(originalRegions[index])) {
      clonedRegions[index].setAttribute('data-threads-inner-text', originalRegions[index].innerText || '');
    }
  }
  const mediaIsReady = (element) => {
    if (element instanceof HTMLImageElement) return element.complete && element.naturalWidth > 0;
    if (element instanceof HTMLVideoElement) return element.readyState >= 1 || Boolean(element.poster);
    if (element instanceof HTMLIFrameElement) return Boolean(element.contentDocument?.readyState === 'complete');
    return false;
  };
  const originalMedia = Array.from(card.querySelectorAll('img,video,iframe'));
  const clonedMedia = Array.from(clone.querySelectorAll('img,video,iframe'));
  if (originalMedia.length !== clonedMedia.length) return { reason: 'media_snapshot_mismatch' };
  for (let index = 0; index < originalMedia.length; index += 1) {
    clonedMedia[index].setAttribute('data-threads-media-ready', mediaIsReady(originalMedia[index]) ? 'true' : 'false');
  }
  return {
    html: clone.outerHTML,
    timestamp: time?.getAttribute('datetime') || null,
    isLoading: Boolean(card.querySelector('[aria-busy="true"],[role="progressbar"]')),
    hasUnreadyMedia: originalMedia.some((element) => !mediaIsReady(element))
  };
}"""


async def _get_browser() -> Any:
    """Start Chromium once per backend process and reuse it between keywords."""
    global _playwright_instance, _browser_instance
    if _browser_instance is not None and _browser_instance.is_connected():
        return _browser_instance
    if _playwright_instance is not None:
        await _playwright_instance.stop()
    from playwright.async_api import async_playwright

    _playwright_instance = await async_playwright().start()
    _browser_instance = await _playwright_instance.chromium.launch(
        headless=True,
        args=["--disable-dev-shm-usage", "--no-sandbox"],
    )
    return _browser_instance


async def close_threads_web_browser() -> None:
    """Close the shared Chromium process during backend shutdown."""
    global _playwright_instance, _browser_instance
    async with _browser_lock:
        if _browser_instance is not None and _browser_instance.is_connected():
            await _browser_instance.close()
        if _playwright_instance is not None:
            await _playwright_instance.stop()
        _browser_instance = None
        _playwright_instance = None


async def _new_authenticated_context(db: AsyncSession) -> Any:
    storage_state = await get_setting(db, _SESSION_KEY)
    if not isinstance(storage_state, dict):
        raise ThreadsCollectionError("Threads web session is not configured. Import a Playwright storage state first.")
    browser = await _get_browser()
    return await browser.new_context(
        storage_state=storage_state,
        viewport={"width": 1440, "height": 1000},
        locale="ru-RU",
        timezone_id="Asia/Almaty",
    )


async def _raise_for_auth_or_challenge(page: Any) -> None:
    current_url = page.url.casefold()
    if "/login" in current_url or "/accounts/login" in current_url:
        raise ThreadsCollectionError("Threads web session has expired. Import a fresh Playwright storage state.")
    if "/challenge" in current_url or "/checkpoint" in current_url:
        raise ThreadsCollectionError("Threads requested an account verification step. Refresh the browser session.")


def _merge_search_cards(
    found: dict[str, DiscoveredPost],
    extractions: dict[str, PostExtraction],
    state: dict[str, Any],
) -> int:
    """Keep each card's identity-checked text before search virtualization removes it."""
    items = state.get("posts")
    new_count = merge_discovery_batch(found, items)
    assert isinstance(items, list)
    for item in items:
        candidate = found[item["id"]]
        html = item.get("html")
        if not isinstance(html, str) or not html:
            parsed = _search_failed_result(
                candidate,
                ExtractionStatus.INCOMPLETE,
                "search_card_html_missing",
                timestamp=item.get("timestamp") if isinstance(item.get("timestamp"), str) else None,
            )
        else:
            parsed = parse_post_card_html(
                html,
                expected_post_id=candidate.post_id,
                expected_username=candidate.username,
                permalink=candidate.permalink,
                timestamp=item.get("timestamp") if isinstance(item.get("timestamp"), str) else None,
            )
            parsed.source = SEARCH_DOM_SOURCE
            parsed.parser_version = SEARCH_PARSER_VERSION
            if item.get("is_truncated") and parsed.status == ExtractionStatus.SUCCESS:
                parsed = _search_failed_result(
                    candidate, ExtractionStatus.INCOMPLETE, "search_card_text_truncated", timestamp=parsed.timestamp
                )
            elif item.get("is_loading") and not parsed.confirmed:
                parsed = _search_failed_result(
                    candidate, ExtractionStatus.INCOMPLETE, "search_card_content_still_loading", timestamp=parsed.timestamp
                )
        parsed.likes_count = candidate.likes_count or 0
        parsed.replies_count = candidate.replies_count or 0
        previous = extractions.get(candidate.post_id)
        priority = {
            ExtractionStatus.SUCCESS: 3,
            ExtractionStatus.NO_TEXT: 2,
            ExtractionStatus.INCOMPLETE: 1,
            ExtractionStatus.UNKNOWN_STRUCTURE: 1,
            ExtractionStatus.UNAVAILABLE: 0,
        }
        if previous is None or (
            (priority.get(parsed.status, 0), len(parsed.text or ""))
            > (priority.get(previous.status, 0), len(previous.text or ""))
        ):
            extractions[candidate.post_id] = parsed
        elif previous is not None:
            previous.likes_count = candidate.likes_count or 0
            previous.replies_count = candidate.replies_count or 0
    return new_count


def _search_failed_result(
    candidate: DiscoveredPost,
    status: ExtractionStatus,
    reason: str,
    *,
    timestamp: str | None = None,
) -> PostExtraction:
    return PostExtraction(
        post_id=candidate.post_id,
        username=candidate.username,
        permalink=candidate.permalink,
        text=None,
        status=status,
        source=SEARCH_DOM_SOURCE,
        parser_version=SEARCH_PARSER_VERSION,
        reason=reason,
        timestamp=timestamp,
    )


async def discover_keyword_posts(db: AsyncSession, phrase: str) -> DiscoveryResult:
    """Capture and parse identity-matched search cards as the feed scrolls."""
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise ThreadsCollectionError("Playwright is not installed in the backend runtime") from exc

    async with _browser_lock:
        context = None
        try:
            context = await _new_authenticated_context(db)
            page = await context.new_page()
            response = await page.goto(
                _SEARCH_URL.format(query=quote_plus(phrase)),
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            await _raise_for_auth_or_challenge(page)
            if response is not None and response.status >= 500:
                raise ThreadsCollectionError("Threads web search returned a server error")
            try:
                await page.wait_for_function(_SEARCH_COLUMN_READY_SCRIPT, timeout=15_000)
            except PlaywrightTimeoutError as exc:
                logger.error(
                    "Threads extraction diagnostic post_id=* source=%s parser_version=%s status=%s reason=%s",
                    "threads_search_dom",
                    SEARCH_PARSER_VERSION,
                    ExtractionStatus.UNKNOWN_STRUCTURE.value,
                    "search_column_not_found",
                )
                raise ThreadsCollectionError("Threads search structure changed: results column was not found") from exc

            found: dict[str, DiscoveredPost] = {}
            extractions: dict[str, PostExtraction] = {}
            state = await page.evaluate(_SEARCH_DISCOVERY_SCRIPT)
            if not isinstance(state, dict) or not state.get("structure"):
                raise ThreadsCollectionError("Threads search structure changed: results column is unreadable")
            try:
                _merge_search_cards(found, extractions, state)
            except ValueError as exc:
                raise ThreadsCollectionError(f"Threads search structure changed: {exc}") from exc
            rounds = 0
            stable_rounds = 0
            loading_stalled_rounds = 0
            loading_stalled = False
            limit_reached = False
            while rounds < _MAX_SEARCH_SCROLL_ROUNDS:
                if len(found) >= _MAX_DISCOVERED_POSTS:
                    limit_reached = True
                    break

                before_signature = str(state.get("signature", ""))
                before_height = int(state.get("scrollHeight", 0))
                before_loading = bool(state.get("is_loading"))
                scroll_step = await page.evaluate(
                    """() => {
                      const column = document.querySelector('[aria-label="Column body"]');
                      const scroller = column && column.scrollHeight > column.clientHeight + 1
                        ? column
                        : document.scrollingElement;
                      const step = Math.max(500, Math.floor(Math.min(scroller?.clientHeight || window.innerHeight, window.innerHeight) * 0.8));
                      if (scroller) scroller.scrollTop += step;
                      else window.scrollBy(0, step);
                      return step;
                    }"""
                )
                near_end = (
                    int(state.get("scrollTop", 0))
                    + int(state.get("clientHeight", 0))
                    + int(scroll_step or 0)
                    >= before_height - 50
                )
                try:
                    await page.wait_for_function(
                        _SEARCH_STATE_CHANGED_SCRIPT,
                        arg={
                            "signature": before_signature,
                            "scrollHeight": before_height,
                            "isLoading": before_loading,
                        },
                        timeout=_SEARCH_CHANGE_TIMEOUT_MS if near_end else _SEARCH_INTERIOR_CHANGE_TIMEOUT_MS,
                    )
                except PlaywrightTimeoutError:
                    pass
                next_state = await page.evaluate(_SEARCH_DISCOVERY_SCRIPT)
                if not isinstance(next_state, dict) or not next_state.get("structure"):
                    raise ThreadsCollectionError("Threads search structure changed while paginating results")
                before_count = len(found)
                try:
                    _merge_search_cards(found, extractions, next_state)
                except ValueError as exc:
                    raise ThreadsCollectionError(f"Threads search structure changed while paginating: {exc}") from exc
                new_unique_ids = len(found) - before_count
                rounds += 1
                state_is_stable = pagination_round_is_stable(
                    previous_signature=str(state.get("signature", "")),
                    previous_height=int(state.get("scrollHeight", 0)),
                    next_signature=str(next_state.get("signature", "")),
                    next_height=int(next_state.get("scrollHeight", 0)),
                    new_unique_ids=new_unique_ids,
                )
                at_end = (
                    int(next_state.get("scrollTop", 0)) + int(next_state.get("clientHeight", 0))
                    >= int(next_state.get("scrollHeight", 0)) - 50
                )
                if state_is_stable and at_end and next_state.get("is_loading"):
                    loading_stalled_rounds += 1
                else:
                    loading_stalled_rounds = 0
                if state_is_stable and at_end and not next_state.get("is_loading"):
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                state = next_state
                if stable_rounds >= 2:
                    break
                if loading_stalled_rounds >= 3:
                    loading_stalled = True
                    break

            if not found:
                logger.error(
                    "Threads extraction diagnostic post_id=* source=%s parser_version=%s status=%s reason=%s",
                    "threads_search_dom",
                    SEARCH_PARSER_VERSION,
                    ExtractionStatus.UNKNOWN_STRUCTURE.value,
                    "search_page_has_no_canonical_post_permalink",
                )
                raise ThreadsCollectionError("Threads search returned no canonical post permalinks; result structure is unverified")
            # Threads exposes no end-of-feed marker in the inspected structure.
            # A stable virtualized DOM is useful to stop scrolling, but it is not
            # proof that the server has no further pages.
            complete = False
            return DiscoveryResult(
                posts=list(found.values()),
                extractions=extractions,
                complete=complete,
                rounds=rounds,
                reason="maximum_discovered_post_limit_reached"
                if limit_reached
                else (
                    "search_loading_stalled_before_end"
                    if loading_stalled
                    else (
                        "search_end_marker_not_observed"
                        if stable_rounds >= 2
                        else "search_scroll_limit_reached_before_stable_end"
                    )
                ),
            )
        except ThreadsCollectionError:
            raise
        except Exception as exc:
            logger.warning("Threads search discovery failed (%s)", type(exc).__name__)
            raise ThreadsCollectionError("Threads web search failed. Check the imported session and try again.") from exc
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    logger.debug("Could not close the Threads browser context cleanly")


def _failed_result(candidate: DiscoveredPost, status: ExtractionStatus, reason: str) -> PostExtraction:
    return PostExtraction(
        post_id=candidate.post_id,
        username=candidate.username,
        permalink=candidate.permalink,
        text=None,
        status=status,
        source=POST_DOM_SOURCE,
        parser_version=PARSER_VERSION,
        reason=reason,
    )


def _route_matches(url: str, candidate: DiscoveredPost) -> bool:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    return (
        parsed.hostname in {"threads.com", "www.threads.com", "threads.net", "www.threads.net"}
        and len(parts) == 3
        and parts[0].casefold() == f"@{candidate.username}".casefold()
        and parts[1] == "post"
        and parts[2] == candidate.post_id
    )


async def fetch_post_pages(db: AsyncSession, candidates: list[DiscoveredPost]) -> dict[str, PostExtraction]:
    """Visit each exact permalink and parse only the identity-matched post root."""
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise ThreadsCollectionError("Playwright is not installed in the backend runtime") from exc

    results: dict[str, PostExtraction] = {}
    eligible = candidates[:_MAX_POST_FETCHES_PER_RUN]
    overflow = candidates[_MAX_POST_FETCHES_PER_RUN:]
    for candidate in overflow:
        results[candidate.post_id] = _failed_result(
            candidate,
            ExtractionStatus.INCOMPLETE,
            "maximum_post_fetch_limit_reached",
        )
    if not eligible:
        return results

    async with _browser_lock:
        context = None
        try:
            context = await _new_authenticated_context(db)
            page = await context.new_page()
            for candidate_index, candidate in enumerate(eligible):
                last_result: PostExtraction | None = None
                session_failure_reason: str | None = None
                for attempt in range(_POST_FETCH_ATTEMPTS):
                    try:
                        try:
                            response = await page.goto(candidate.permalink, wait_until="domcontentloaded", timeout=45_000)
                        except PlaywrightTimeoutError:
                            last_result = _failed_result(candidate, ExtractionStatus.INCOMPLETE, "post_navigation_timeout")
                            continue
                        try:
                            await _raise_for_auth_or_challenge(page)
                        except ThreadsCollectionError as exc:
                            session_failure_reason = (
                                "session_expired"
                                if "expired" in str(exc).casefold()
                                else "account_verification_required"
                            )
                            last_result = _failed_result(candidate, ExtractionStatus.INCOMPLETE, session_failure_reason)
                            break
                        status_code = response.status if response is not None else None
                        if status_code in {404, 410}:
                            last_result = _failed_result(candidate, ExtractionStatus.UNAVAILABLE, f"http_{status_code}")
                            break
                        if status_code is not None and (status_code >= 500 or status_code in {403, 429}):
                            last_result = _failed_result(candidate, ExtractionStatus.INCOMPLETE, f"http_{status_code}")
                            continue
                        if not _route_matches(page.url, candidate):
                            last_result = _failed_result(candidate, ExtractionStatus.UNKNOWN_STRUCTURE, "landed_on_different_post_route")
                            break
                        expected = {"postId": candidate.post_id, "username": candidate.username}
                        await page.wait_for_function(
                            _POST_ROOT_AND_READY_SCRIPT,
                            arg=expected,
                            timeout=_POST_READY_TIMEOUT_MS,
                        )
                        snapshot = await page.evaluate(_POST_SNAPSHOT_SCRIPT, expected)
                        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("html"), str):
                            reason = snapshot.get("reason") if isinstance(snapshot, dict) else "post_dom_snapshot_invalid"
                            last_result = _failed_result(candidate, ExtractionStatus.UNKNOWN_STRUCTURE, str(reason or "post_dom_snapshot_invalid"))
                            break
                        last_result = parse_post_card_html(
                            snapshot["html"],
                            expected_post_id=candidate.post_id,
                            expected_username=candidate.username,
                            permalink=candidate.permalink,
                            timestamp=snapshot.get("timestamp") if isinstance(snapshot.get("timestamp"), str) else None,
                        )
                        break
                    except PlaywrightTimeoutError:
                        snapshot = await page.evaluate(_POST_SNAPSHOT_SCRIPT, {
                            "postId": candidate.post_id,
                            "username": candidate.username,
                        })
                        if isinstance(snapshot, dict) and isinstance(snapshot.get("html"), str):
                            parsed = parse_post_card_html(
                                snapshot["html"],
                                expected_post_id=candidate.post_id,
                                expected_username=candidate.username,
                                permalink=candidate.permalink,
                                timestamp=snapshot.get("timestamp") if isinstance(snapshot.get("timestamp"), str) else None,
                            )
                            if parsed.confirmed:
                                last_result = parsed
                            elif (
                                snapshot.get("isLoading")
                                or snapshot.get("hasUnreadyMedia")
                                or parsed.reason == "body_region_is_present_but_empty"
                            ):
                                last_result = _failed_result(candidate, ExtractionStatus.INCOMPLETE, "target_post_content_still_loading")
                            else:
                                last_result = parsed
                        else:
                            reason = snapshot.get("reason") if isinstance(snapshot, dict) else "target_post_identity_not_ready"
                            normalized_reason = str(reason or "target_post_identity_not_ready")
                            status = (
                                ExtractionStatus.INCOMPLETE
                                if normalized_reason == "target_permalink_anchor_missing"
                                else ExtractionStatus.UNKNOWN_STRUCTURE
                            )
                            last_result = _failed_result(candidate, status, normalized_reason)
                    except ThreadsCollectionError:
                        raise
                    except Exception as exc:
                        last_result = _failed_result(candidate, ExtractionStatus.INCOMPLETE, f"page_fetch_error:{type(exc).__name__}")
                    if last_result.status not in {ExtractionStatus.INCOMPLETE, ExtractionStatus.UNAVAILABLE}:
                        break
                    if attempt + 1 < _POST_FETCH_ATTEMPTS:
                        continue

                if last_result is None:
                    last_result = _failed_result(candidate, ExtractionStatus.INCOMPLETE, "post_fetch_attempts_exhausted")
                results[candidate.post_id] = last_result
                logger.info(
                    "Threads extraction diagnostic post_id=%s source=%s parser_version=%s status=%s reason=%s",
                    last_result.post_id,
                    last_result.source,
                    last_result.parser_version,
                    last_result.status.value,
                    last_result.reason,
                )
                if session_failure_reason is not None:
                    for remaining in eligible[candidate_index + 1:]:
                        results[remaining.post_id] = _failed_result(
                            remaining,
                            ExtractionStatus.INCOMPLETE,
                            session_failure_reason,
                        )
                    break
        except ThreadsCollectionError:
            raise
        except Exception as exc:
            logger.warning("Threads post fetch batch failed (%s)", type(exc).__name__)
            for candidate in eligible:
                results.setdefault(
                    candidate.post_id,
                    _failed_result(candidate, ExtractionStatus.INCOMPLETE, f"browser_batch_error:{type(exc).__name__}"),
                )
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    logger.debug("Could not close the Threads browser context cleanly")
    return results


async def search_keyword_web(db: AsyncSession, phrase: str) -> tuple[list[PostExtraction], bool, str | None]:
    """Compatibility facade for callers that need parsed search results."""
    discovery = await discover_keyword_posts(db, phrase)
    extractions = [
        discovery.extractions.get(candidate.post_id)
        or _search_failed_result(candidate, ExtractionStatus.INCOMPLETE, "search_card_extraction_result_missing")
        for candidate in discovery.posts
    ]
    return extractions, discovery.complete, discovery.reason
