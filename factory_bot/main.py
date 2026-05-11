"""Точка входа @StrizhovFactoryBot."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from factory_bot.config import load_settings, setup_logging
from factory_bot.handlers import commands as cmd_handlers
from factory_bot.handlers import messages as msg_handlers
from factory_bot.services.brain import Brain
from factory_bot.services.competitors import CompetitorScraper
from factory_bot.services.db import DB
from factory_bot.services.factory import Factory
from factory_bot.services.scheduler import WeeklyScheduler


SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"


async def main() -> None:
    cfg = load_settings()
    setup_logging(cfg.log_level)
    log = logging.getLogger("bot")
    log.info("Старт @StrizhovFactoryBot")

    # --- сервисы ---
    db = DB(cfg.db_path)
    await db.init()

    brain = Brain(
        api_key=cfg.deepseek_api_key,
        base_url=cfg.deepseek_base_url,
        model=cfg.deepseek_model,
    )
    scraper = CompetitorScraper()
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    factory = Factory(db=db, brain=brain, scraper=scraper, system_prompt=system_prompt)

    # --- aiogram ---
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(cmd_handlers.build_router(cfg.owner_tg_id, db, factory))
    dp.include_router(msg_handlers.build_router(cfg.owner_tg_id, db, brain,
                                                 system_prompt, cfg.memory_turns))

    # --- понедельная авто-планёрка ---
    async def weekly_callback() -> None:
        if await db.is_paused(cfg.owner_tg_id):
            log.info("Понедельный pack пропущен — пауза"); return
        log.info("Понедельный pack: старт")
        try:
            result = await factory.pack(topic=None)
            await bot.send_message(cfg.owner_tg_id, "🗓 Понедельный пакет\n\n" + result["summary"])
        except Exception:
            log.exception("Понедельный pack упал")

    scheduler = WeeklyScheduler(
        timezone=cfg.timezone, day_of_week=cfg.weekly_pack_day,
        time_str=cfg.weekly_pack_time, callback=weekly_callback,
    )
    scheduler.start()

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
