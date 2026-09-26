"""Open Threads for a manual sign-in and export a Playwright storage state."""

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def export_session(destination: Path) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto("https://www.threads.com/login", wait_until="domcontentloaded")
            print("Войдите в Threads в открывшемся окне и вернитесь сюда.")
            input("После входа нажмите Enter, чтобы сохранить web-сессию: ")
            state = await context.storage_state()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Playwright storage state сохранён: {destination}")
            print("Это секрет авторизации. Не отправляйте файл и храните его в безопасном месте.")
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / "threads-storage-state.json",
        help="куда сохранить сессию (по умолчанию: ~/threads-storage-state.json)",
    )
    args = parser.parse_args()
    asyncio.run(export_session(args.output.expanduser()))


if __name__ == "__main__":
    main()
