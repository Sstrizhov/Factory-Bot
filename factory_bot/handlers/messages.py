"""Хендлер свободного текста — отвечает Brain в стиле бренда МСВ."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from factory_bot.services.brain import Brain
from factory_bot.services.db import DB

log = logging.getLogger(__name__)


def build_router(owner_id: int, db: DB, brain: Brain, system_prompt: str,
                 memory_turns: int) -> Router:
    router = Router(name="messages")

    @router.message(F.text & ~F.text.startswith("/"))
    async def on_text(msg: Message) -> None:
        if msg.from_user is None or msg.from_user.id != owner_id:
            return
        text = msg.text or ""
        await db.add_message(owner_id, "user", text)
        history = await db.last_messages(owner_id, memory_turns)
        # Последнее сообщение в истории — это уже наш user_text. Берём всё кроме него.
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
            await msg.answer("DeepSeek подвис. Попробуй через минуту."); return
        await db.add_message(owner_id, "assistant", reply)
        for chunk in _split(reply, 3500):
            await msg.answer(chunk)

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
