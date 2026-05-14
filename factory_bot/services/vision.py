"""Анализ изображений через Groq Llama-3.2-Vision (бесплатно).

Один и тот же GROQ_API_KEY используется для Whisper и для Vision —
ничего дополнительно регистрировать не нужно.
"""
from __future__ import annotations

import base64
import logging

from openai import AsyncOpenAI

log = logging.getLogger(__name__)


class VisionAnalyzer:
    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def describe(self, image_bytes: bytes, question: str = "",
                        mime: str = "image/jpeg") -> str:
        """Анализировать изображение. Возвращает текст."""
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"
        prompt = question.strip() or (
            "Опиши это изображение по существу. Если на нём текст — извлеки его. "
            "Если это пост из соцсети — пересскажи смысл и формат (хук, тело, CTA). "
            "Если это скрин интерфейса — опиши, что в нём важное."
        )
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }],
                max_tokens=1024,
                temperature=0.3,
            )
        except Exception:
            log.exception("Vision analyze failed")
            raise
        return (resp.choices[0].message.content or "").strip()
