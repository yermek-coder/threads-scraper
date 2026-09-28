"""Top has an independent timer and manual collection requests both sections."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.keywords import collect_one_keyword
from app.services import scheduler


class TopScheduleTests(unittest.IsolatedAsyncioTestCase):
    def test_top_timer_is_six_hours(self) -> None:
        fake = MagicMock(running=False)
        with patch.object(scheduler, "scheduler", fake):
            scheduler.start_scheduler()
        jobs = {call.kwargs["id"]: call for call in fake.add_job.call_args_list}
        self.assertEqual(
            jobs["threads-top-poll"].kwargs["trigger"].interval.total_seconds(), 21600
        )
        self.assertEqual(
            jobs["threads-top-poll"].kwargs["kwargs"], {"search_sections": ("top",)}
        )
        self.assertNotIn("kwargs", jobs["threads-keyword-poll"].kwargs)

    async def test_manual_keyword_requests_both(self) -> None:
        keyword = MagicMock()
        db = AsyncMock()
        db.get.return_value = keyword
        with patch(
            "app.api.keywords.collect_keyword", new_callable=AsyncMock, return_value={}
        ) as collect:
            await collect_one_keyword(1, db)
        collect.assert_awaited_once_with(db, keyword, search_sections=("recent", "top"))
