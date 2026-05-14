"""Парсер URL: достаём основной текст со страницы.

Поддерживает статический HTML. JS-rendered контент (SPA) не парсим —
ограничение MVP. Для большинства статей и постов хватает.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# URL regex — достаточно простой для извлечения http(s) ссылок из текста
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)

MAX_TEXT_CHARS = 8000  # Сколько максимум вернуть текста (чтобы не съесть контекст LLM)


@dataclass
class FetchedPage:
    url: str
    title: str
    text: str            # очищенный основной текст
    error: Optional[str] = None


def extract_urls(text: str) -> list[str]:
    """Найти все URL в тексте."""
    return list(dict.fromkeys(URL_RE.findall(text)))  # уникальные, в порядке появления


class URLFetcher:
    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout

    async def fetch(self, url: str) -> FetchedPage:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": UA},
            ) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return FetchedPage(url=url, title="", text="",
                                       error=f"HTTP {resp.status_code}")
                ctype = resp.headers.get("content-type", "")
                if "text/html" not in ctype and "application/xhtml" not in ctype:
                    # Не HTML — возвращаем как есть, обрезанный
                    return FetchedPage(
                        url=url, title="",
                        text=resp.text[:MAX_TEXT_CHARS],
                    )
                return self._parse(url, resp.text)
        except Exception as exc:
            log.warning("URL fetch failed: %s — %s", url, exc)
            return FetchedPage(url=url, title="", text="", error=str(exc))

    def _parse(self, url: str, html: str) -> FetchedPage:
        soup = BeautifulSoup(html, "html.parser")

        # Удаляем шум
        for tag in soup(["script", "style", "noscript", "iframe", "svg", "nav",
                          "footer", "header", "aside"]):
            tag.decompose()

        # Title
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        elif soup.find("h1"):
            title = soup.find("h1").get_text(strip=True)

        # Основной текст — приоритет article/main, иначе body
        main = soup.find("article") or soup.find("main") or soup.body or soup
        text = main.get_text("\n", strip=True) if main else ""

        # Удаляем повторяющиеся пустые строки
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n\n[...текст обрезан...]"
        return FetchedPage(url=url, title=title, text=text)


async def fetch_many(fetcher: URLFetcher, urls: list[str]) -> list[FetchedPage]:
    """Параллельная выкачка нескольких URL."""
    import asyncio
    return await asyncio.gather(*(fetcher.fetch(u) for u in urls))
