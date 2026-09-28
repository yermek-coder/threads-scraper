"""APScheduler lifecycle and recurring Threads polling/token refresh jobs."""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import config
from app.core.database import SessionFactory
from app.services.collector import poll_active_keywords
from app.services.settings_service import get_setting
from app.services.threads_client import refresh_access_token

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="UTC")


async def refresh_token_if_due() -> None:
    """Refresh the Threads token when its last refresh is older than configured interval."""
    async with SessionFactory() as db:
        token = await get_setting(db, "threads_access_token")
        if not token:
            return
        last_refresh = await get_setting(db, "threads_last_token_refresh")
        if isinstance(last_refresh, str):
            try:
                refresh_at = datetime.fromisoformat(last_refresh)
                if refresh_at.tzinfo is None:
                    refresh_at = refresh_at.replace(tzinfo=timezone.utc)
                due_at = refresh_at + timedelta(days=config.threads_token_refresh_interval_days)
                if datetime.now(timezone.utc) < due_at:
                    return
            except ValueError:
                logger.warning("Invalid stored Threads token refresh timestamp; refreshing now")
        try:
            await refresh_access_token(db)
            logger.info("Threads access token refreshed")
        except Exception:
            logger.exception("Scheduled Threads token refresh failed")


def start_scheduler() -> None:
    """Start scheduled jobs if the scheduler is not already running."""
    if scheduler.running:
        return
    scheduler.add_job(
        poll_active_keywords,
        trigger=IntervalTrigger(minutes=config.threads_poll_interval_minutes),
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=15),
        id="threads-keyword-poll",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        poll_active_keywords,
        trigger=IntervalTrigger(hours=6),
        kwargs={"search_sections": ("top",)},
        id="threads-top-poll",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        refresh_token_if_due,
        trigger=IntervalTrigger(hours=6),
        id="threads-token-refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()


def stop_scheduler() -> None:
    """Stop scheduled jobs without waiting for remote API calls."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
