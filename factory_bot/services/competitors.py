"""Web-парсер публичных Telegram-каналов через t.me/s/<channel>.

Без авторизации, без api_id. Достаточно для трендов и анализа.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, asdict
from typing import Optional

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class ChannelPost:
    channel: str
    post_id: str
    url: str
    text: str
    views: Optional[int]
    published_at: Optional[str]
    has_media: bool

    def to_dict(self) -> dict:
        return asdict(self)


class CompetitorScraper:
    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout

    async def fetch_channel(self, username: str, limit: int = 20) -> list[ChannelPost]:
        username = username.lstrip("@").strip()
        url = f"https://t.me/s/{username}"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": UA},
            ) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    log.warning("t.me/s/%s -> %s", username, resp.status_code)
                    return []
                return self._parse(username, resp.text, limit)
        except Exception:
            log.exception("Не удалось загрузить t.me/s/%s", username)
            return []

    def _parse(self, channel: str, html: str, limit: int) -> list[ChannelPost]:
        soup = BeautifulSoup(html, "html.parser")
        posts: list[ChannelPost] = []
        for wrap in soup.select(".tgme_widget_message_wrap")[-limit:][::-1]:
            msg = wrap.select_one(".tgme_widget_message")
            if msg is None:
                continue
            data_post = msg.get("data-post", "")
            post_id = data_post.split("/")[-1] if data_post else ""
            url = f"https://t.me/{channel}/{post_id}" if post_id else ""
            text_el = msg.select_one(".tgme_widget_message_text")
            text = text_el.get_text("\n", strip=True) if text_el else ""
            views_el = msg.select_one(".tgme_widget_message_views")
            views = self._parse_views(views_el.get_text(strip=True)) if views_el else None
            time_el = msg.select_one(".tgme_widget_message_date time")
            published_at = time_el.get("datetime") if time_el else None
            has_media = bool(
                msg.select_one(".tgme_widget_message_photo_wrap")
                or msg.select_one(".tgme_widget_message_video")
                or msg.select_one(".tgme_widget_message_document")
            )
            posts.append(ChannelPost(
                channel=channel, post_id=post_id, url=url, text=text,
                views=views, published_at=published_at, has_media=has_media,
            ))
        return posts

    @staticmethod
    def _parse_views(s: str) -> Optional[int]:
        s = s.strip().upper().replace(",", ".")
        m = re.match(r"([\d.]+)([KM]?)", s)
        if not m:
            return None
        try:
            num = float(m.group(1))
        except ValueError:
            return None
        if m.group(2) == "K":
            num *= 1_000
        elif m.group(2) == "M":
            num *= 1_000_000
        return int(num)


async def scrape_many(scraper: CompetitorScraper, usernames: list[str],
                      limit_per_channel: int = 10,
                      concurrency: int = 5) -> dict[str, list[ChannelPost]]:
    sem = asyncio.Semaphore(concurrency)
    result: dict[str, list[ChannelPost]] = {}

    async def one(name: str) -> None:
        async with sem:
            result[name] = await scraper.fetch_channel(name, limit_per_channel)

    await asyncio.gather(*(one(u) for u in usernames))
    return result
