"""Высокоуровневые операции контент-фабрики.

`pack(topic)` — главная функция: собирает данные (кейсы + конкуренты),
формирует большой промпт для LLM, парсит ответ, сохраняет драфты в БД,
возвращает сводку для Сергея.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Optional

from .brain import Brain
from .competitors import CompetitorScraper, scrape_many
from .db import DB

log = logging.getLogger(__name__)


PACK_USER_TEMPLATE = """\
Сергею нужен пакет контента{topic_phrase}.

# КЕЙСЫ КЛИЕНТОВ (используй как материал)

{cases_block}

# СВЕЖИЕ ПОСТЫ КОНКУРЕНТОВ (что сейчас работает в нише)

{competitors_block}

# ЗАДАЧА

Сгенерируй пакет:
- 5 сценариев Reels (с хуками 2 сек, удержанием 12-30 сек, CTA)
- 5 ТГ-постов (короткие, по 1-3 абзаца, в тоне МСВ)
- 3 ТЗ для каруселей Instagram (5-7 слайдов каждая)

# ФОРМАТ ОТВЕТА

Верни СТРОГО JSON-массив объектов. Никакого текста до или после JSON.
Каждый объект:
{{
  "type": "reels_script" | "tg_post" | "carousel_brief",
  "title": "короткое имя 3-7 слов",
  "body": "полный текст сценария / поста / ТЗ карусели"
}}

В body для Reels — хук, удержание, CTA, разделённые на абзацы.
В body для карусели — слайд 1, слайд 2, ..., слайд N (один абзац на слайд).
В body для ТГ-поста — финальный текст готовый к публикации.

Всего объектов: 13 (5+5+3).
"""


class Factory:
    def __init__(self, db: DB, brain: Brain, scraper: CompetitorScraper,
                 system_prompt: str):
        self.db = db
        self.brain = brain
        self.scraper = scraper
        self._system = system_prompt

    async def pack(self, topic: Optional[str] = None) -> dict:
        """Собрать пакет контента. Возвращает {summary: str, ids: list[int], errors: list[str]}."""
        errors: list[str] = []

        # 1. Кейсы
        cases = await self.db.cases_all()
        cases_block = self._format_cases(cases) if cases else "(пока не добавлены — добавь через /case)"

        # 2. Конкуренты
        channels = await self.db.channels_active()
        if channels:
            usernames = [c["username"] for c in channels]
            try:
                scraped = await scrape_many(self.scraper, usernames, limit_per_channel=8)
                for u in usernames:
                    await self.db.channel_mark_scanned(u)
                competitors_block = self._format_competitors(scraped)
            except Exception as exc:
                log.exception("Scrape failed")
                competitors_block = "(не удалось загрузить)"
                errors.append(f"Парсер конкурентов упал: {exc}")
        else:
            competitors_block = "(пока не добавлены — добавь через /channel <user>)"

        # 3. Промпт для LLM
        topic_phrase = f' на тему «{topic}»' if topic else ""
        user_prompt = PACK_USER_TEMPLATE.format(
            topic_phrase=topic_phrase,
            cases_block=cases_block,
            competitors_block=competitors_block,
        )

        # 4. Вызов LLM
        try:
            response = await self.brain.chat(
                system=self._system,
                user=user_prompt,
                temperature=0.7,
                max_tokens=4096,
            )
        except Exception as exc:
            return {"summary": f"DeepSeek упал: {exc}", "ids": [], "errors": errors + [str(exc)]}

        # 5. Парсим JSON
        items = self._extract_json_array(response)
        if not items:
            errors.append("LLM не вернул валидный JSON. Сырой ответ сохранён в логах.")
            log.warning("Raw LLM response (no JSON):\n%s", response[:2000])
            return {"summary": "Не удалось распарсить ответ LLM. Логи в Railway.",
                    "ids": [], "errors": errors}

        # 6. Сохраняем в БД
        ids: list[int] = []
        for item in items:
            try:
                pid = await self.db.piece_save(
                    type_=item.get("type", "tg_post"),
                    title=item.get("title", "(без заголовка)"),
                    body=item.get("body", ""),
                )
                ids.append(pid)
            except Exception as exc:
                errors.append(f"Сохранение упало: {exc}")

        # 7. Сводка для Сергея
        summary_lines = [
            f"Пакет собран. Драфтов: {len(ids)}.",
            "",
            f"Кейсы использованы: {len(cases)}.",
            f"Каналы конкурентов отсканированы: {len(channels)}.",
            "",
            "Драфты:",
        ]
        for item, pid in zip(items, ids):
            type_short = {
                "reels_script": "Reels",
                "tg_post": "ТГ-пост",
                "carousel_brief": "Карусель",
            }.get(item.get("type", ""), item.get("type", ""))
            summary_lines.append(f"  #{pid} [{type_short}] {item.get('title', '')}")
        summary_lines.append("")
        summary_lines.append("Полные тексты — /drafts. Одобрить — /approve N.")
        if errors:
            summary_lines.append("")
            summary_lines.append("⚠ Замечания:")
            summary_lines.extend(f"  — {e}" for e in errors)

        return {"summary": "\n".join(summary_lines), "ids": ids, "errors": errors}

    @staticmethod
    def _format_cases(cases: list[dict]) -> str:
        lines = []
        for c in cases:
            lines.append(
                f"- {c['name']} ({c.get('niche') or 'без ниши'}): "
                f"было — {c.get('background') or '?'}; "
                f"стало — {c.get('result') or '?'}; "
                f"цитаты — {c.get('quotes') or '—'}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_competitors(scraped: dict[str, list]) -> str:
        if not scraped:
            return "(нет данных)"
        chunks: list[str] = []
        for channel, posts in scraped.items():
            if not posts:
                chunks.append(f"## @{channel}\n(не загрузилось)")
                continue
            lines = [f"## @{channel}"]
            for p in posts[:8]:
                text_short = (p.text or "")[:300].replace("\n", " ")
                views_part = f" · {p.views} просм." if p.views else ""
                lines.append(f"- {text_short}{views_part}")
            chunks.append("\n".join(lines))
        return "\n\n".join(chunks)

    @staticmethod
    def _extract_json_array(text: str) -> list[dict]:
        """LLM может обернуть JSON в ```json ... ``` или добавить мусор. Извлекаем массив."""
        # Снимаем возможные ```json ... ```
        m = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
        if m:
            text = m.group(1)
        # Ищем первый [ и последний ] и парсим срез
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except json.JSONDecodeError as exc:
            log.warning("JSON parse failed: %s", exc)
        return []
