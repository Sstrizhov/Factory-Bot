"""Расписание: только понедельная авто-сборка пакета."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)


def _hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":", 1)
    return int(h), int(m)


class WeeklyScheduler:
    def __init__(self, timezone: str, day_of_week: int, time_str: str,
                 callback: Callable[[], Awaitable[None]]):
        self._scheduler = AsyncIOScheduler(timezone=timezone)
        self._day = day_of_week
        self._time = time_str
        self._callback = callback

    def start(self) -> None:
        h, m = _hhmm(self._time)
        self._scheduler.add_job(
            self._fire,
            CronTrigger(day_of_week=self._day, hour=h, minute=m),
            id="weekly_pack",
            replace_existing=True,
        )
        self._scheduler.start()
        log.info("Понедельный pack: день=%s, время=%s", self._day, self._time)

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    async def _fire(self) -> None:
        try:
            await self._callback()
        except Exception:
            log.exception("Понедельный pack упал")
