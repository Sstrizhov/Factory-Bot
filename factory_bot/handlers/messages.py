"""Хендлеры свободного ввода — текст и голосовые сообщения."""
from __future__ import annotations

import io
import logging
from typing import Optional

from aiogram import F, Router
from aiogram.types import Message

from factory_bot.services.brain import Brain
from factory_bot.services.db import DB
from factory_bot.services.voice import VoiceTranscriber

log = logging.getLogger(__name__)


def build_router(owner_id: int, db: DB, brain: Brain,
                 system_prompt: str, memory_turns: int,
                 voice: Optional[VoiceTranscriber] = None) -> Router:
    router = Router(name="messages")

    async def _respond_to(msg: Message, text: str) -> None:
        """Общий обработчик: сохранить, послать в brain, ответить."""
        await db.add_message(owner_id, "user", text)
        history = await db.last_messages(owner_id, memory_turns)
        if history and history[-1].get("role") == "user":
            history = history[:-1]
        await msg.bot.send_chat_action(msg.chat.id, "typing")
        try:
            reply = await brain.chat(
                system=system_prompt, user=text, history=history,
                temperature=0.5, max_tokens=1200,
            )
        except Exception:
            log.exception("brain.chat failed")
            await msg.answer("DeepSeek подвис. Попробуй через минуту.")
            return
        await db.add_message(owner_id, "assistant", reply)
        for chunk in _split(reply, 3500):
            await msg.answer(chunk)

    @router.message(F.text & ~F.text.startswith("/"))
    async def on_text(msg: Message) -> None:
        if msg.from_user is None or msg.from_user.id != owner_id:
            return
        await _respond_to(msg, msg.text or "")

    @router.message(F.voice)
    async def on_voice(msg: Message) -> None:
        if msg.from_user is None or msg.from_user.id != owner_id:
            return
        if voice is None:
            await msg.answer(
                "Голосовые не подключены. Добавь GROQ_API_KEY в Railway Variables."
            )
            return
        if msg.voice is None:
            return
        try:
            file = await msg.bot.get_file(msg.voice.file_id)
            buf = io.BytesIO()
            await msg.bot.download_file(file.file_path, destination=buf)
            audio_bytes = buf.getvalue()
        except Exception:
            log.exception("Voice download failed")
            await msg.answer("Не смог скачать голосовое из Telegram.")
            return
        await msg.bot.send_chat_action(msg.chat.id, "typing")
        try:
            transcript = await voice.transcribe(audio_bytes)
        except Exception:
            log.exception("Voice transcribe failed")
            await msg.answer("Не удалось распознать голосовое.")
            return
        if not transcript:
            await msg.answer("Голосовое распознать не удалось (пусто).")
            return
        # Эхо транскрипта — чтобы Сергей видел, что бот понял.
        await msg.answer(f"🎤 «{transcript}»")
        await _respond_to(msg, transcript)

    return router


def _split(text: str, n: int) -> list[str]:
    if len(text) <= n:
        return [text]
    out: list[str] = []
    cur = ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > n and cur:
            out.append(cur); cur = ""
        cur += line
    if cur:
        out.append(cur)
    return out
