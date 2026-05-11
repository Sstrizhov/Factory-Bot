"""Конфиг — env переменные."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_tg_id: int

    deepseek_api_key: str
    deepseek_model: str
    deepseek_base_url: str

    db_path: Path
    timezone: str

    weekly_pack_day: int       # 0 = понедельник
    weekly_pack_time: str      # 09:00 МСК

    memory_turns: int
    log_level: str


def _req(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Отсутствует обязательная env: {name}")
    return val


def _opt(name: str, default: str) -> str:
    return os.getenv(name) or default


def load_settings() -> Settings:
    return Settings(
        bot_token=_req("BOT_TOKEN"),
        owner_tg_id=int(_req("OWNER_TG_ID")),
        deepseek_api_key=_req("DEEPSEEK_API_KEY"),
        deepseek_model=_opt("DEEPSEEK_MODEL", "deepseek-chat"),
        deepseek_base_url=_opt("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        db_path=Path(_opt("DB_PATH", "./data/factory.db")).expanduser().resolve(),
        timezone=_opt("TZ", "Europe/Moscow"),
        weekly_pack_day=int(_opt("WEEKLY_PACK_DAY", "0")),
        weekly_pack_time=_opt("WEEKLY_PACK_TIME", "09:00"),
        memory_turns=int(_opt("MEMORY_TURNS", "30")),
        log_level=_opt("LOG_LEVEL", "INFO").upper(),
    )


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s · %(levelname)s · %(name)s · %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for noisy in ("aiogram.event", "apscheduler.scheduler", "httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
