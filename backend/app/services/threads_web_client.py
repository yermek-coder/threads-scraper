"""Collect public search results from the authenticated Threads web interface."""

import asyncio
import logging
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings_service import get_setting
from app.services.threads_errors import ThreadsCollectionError

logger = logging.getLogger(__name__)
_SESSION_KEY = "threads_web_storage_state"
_SEARCH_URL = "https://www.threads.com/search?q={query}&serp_type=default&filter=recent"
_browser_lock = asyncio.Lock()
_playwright_instance: Any = None
_browser_instance: Any = None


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


async def search_keyword_web(db: AsyncSession, phrase: str) -> list[dict[str, Any]]:
    """Open Threads search in Chromium and extract post cards from the rendered DOM."""
    storage_state = await get_setting(db, _SESSION_KEY)
    if not isinstance(storage_state, dict):
        raise ThreadsCollectionError("Threads web session is not configured. Import a Playwright storage state first.")

    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise ThreadsCollectionError("Playwright is not installed in the backend runtime") from exc

    async with _browser_lock:
        context = None
        try:
            browser = await _get_browser()
            context = await browser.new_context(
                storage_state=storage_state,
                viewport={"width": 1440, "height": 1000},
                locale="ru-RU",
                timezone_id="Asia/Almaty",
            )
            page = await context.new_page()
            response = await page.goto(
                _SEARCH_URL.format(query=quote_plus(phrase)),
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            if response is not None and response.status >= 500:
                raise ThreadsCollectionError("Threads web search returned a server error")
            try:
                await page.locator('a[href*="/post/"]').first.wait_for(state="attached", timeout=12_000)
            except PlaywrightTimeoutError:
                pass

            if "/login" in page.url or "/accounts/login" in page.url:
                raise ThreadsCollectionError("Threads web session has expired. Import a fresh Playwright storage state.")
            if "/challenge" in page.url or "/checkpoint" in page.url:
                raise ThreadsCollectionError("Threads requested an account verification step. Refresh the browser session.")

            previous_count = 0
            for _ in range(4):
                current_count = await page.locator('a[href*="/post/"]').count()
                if current_count == previous_count and current_count > 0:
                    break
                previous_count = current_count
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1_200)

            records = await page.evaluate(
                """() => {
                  const anchors = Array.from(document.querySelectorAll('a[href*="/post/"]'));
                  const found = new Map();
                  for (const anchor of anchors) {
                    let node = anchor;
                    let card = null;
                    for (let depth = 0; node && depth < 9; depth += 1, node = node.parentElement) {
                      const text = (node.innerText || '').trim();
                      if (!card && text.length >= 20 && text.length <= 5000) card = node;
                      if (node.matches('article,[role="article"]')) break;
                    }
                    if (!card) continue;
                    const url = new URL(anchor.href, location.href);
                    const parts = url.pathname.split('/').filter(Boolean);
                    const postIndex = parts.indexOf('post');
                    const postId = postIndex >= 0 ? parts[postIndex + 1] : '';
                    if (!postId || found.has(postId)) continue;
                    const postText = card.querySelector('span[dir="auto"]')?.innerText || '';
                    const clone = card.cloneNode(true);
                    clone.querySelectorAll('button,time,svg,script,style').forEach((element) => element.remove());
                    const text = (postText || clone.innerText || '').replace(/\\s+/g, ' ').trim();
                    const username = (parts.find((part) => part.startsWith('@')) || '').slice(1);
                    const timestamp = card.querySelector('time[datetime]')?.getAttribute('datetime') || null;
                    found.set(postId, {
                      id: postId,
                      text,
                      username,
                      permalink: url.origin + url.pathname,
                      timestamp,
                      like_count: 0,
                      reply_count: 0,
                      source: 'threads_web_dom'
                    });
                  }
                  return Array.from(found.values());
                }"""
            )
            if not isinstance(records, list):
                raise ThreadsCollectionError("Threads search returned an unreadable page")
            if not records and any(marker in (await page.locator("body").inner_text()).lower() for marker in (
                "log in to threads", "log in to see", "войдите в threads", "войти в threads"
            )):
                raise ThreadsCollectionError("Threads web session is not authenticated. Import a fresh Playwright storage state.")
            return [record for record in records if isinstance(record, dict)]
        except ThreadsCollectionError:
            raise
        except Exception as exc:
            logger.warning("Threads web search failed (%s)", type(exc).__name__)
            raise ThreadsCollectionError("Threads web search failed. Check the imported session and try again.") from exc
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    logger.debug("Could not close the Threads browser context cleanly")
