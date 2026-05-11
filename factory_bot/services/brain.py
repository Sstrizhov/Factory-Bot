"""Клиент DeepSeek (через openai-совместимый SDK).

Простой chat API без function calling — все данные передаются в промпте.
Все 5 ролей агентов реализованы как разные системные промпты + контекст.
"""
from __future__ import annotations

import logging
from typing import Any

from openai import AsyncOpenAI

log = logging.getLogger(__name__)


class Brain:
    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def chat(
        self,
        system: str,
        user: str,
        *,
        history: list[dict] | None = None,
        temperature: float = 0.6,
        max_tokens: int = 2048,
    ) -> str:
        """Один chat-запрос. История — список {'role': 'user'|'assistant', 'content': str}."""
        messages: list[dict] = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user})
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception:
            log.exception("DeepSeek chat failed")
            raise
        return (resp.choices[0].message.content or "").strip()
