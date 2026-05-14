"""Транскрипция голосовых через Groq Whisper API (бесплатный tier).

Groq предоставляет OpenAI-совместимый API. Используем тот же openai SDK,
просто с другим base_url. Лимит free tier: ~28800 секунд аудио в день —
с гигантским запасом для одного пользователя.
"""
from __future__ import annotations

import io
import logging

from openai import AsyncOpenAI

log = logging.getLogger(__name__)


class VoiceTranscriber:
    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def transcribe(self, audio_bytes: bytes,
                          filename: str = "voice.ogg",
                          language: str = "ru") -> str:
        """Транскрибировать аудио в текст. Возвращает чистый текст."""
        buf = io.BytesIO(audio_bytes)
        buf.name = filename  # OpenAI SDK берёт имя для угадывания формата
        try:
            resp = await self._client.audio.transcriptions.create(
                model=self._model,
                file=buf,
                language=language,
            )
        except Exception:
            log.exception("Groq transcribe failed")
            raise
        return (resp.text or "").strip()
