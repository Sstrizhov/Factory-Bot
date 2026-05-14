"""Хендлеры свободного ввода: текст (с URL), голос, документы, фото."""
from __future__ import annotations

import io
import logging
from typing import Optional

from aiogram import F, Router
from aiogram.types import Message

from factory_bot.services.brain import Brain
from factory_bot.services.db import DB
from factory_bot.services.document_parser import parse_document
from factory_bot.services.url_fetcher import URLFetcher, extract_urls, fetch_many
from factory_bot.services.vision import VisionAnalyzer
from factory_bot.services.voice import VoiceTranscriber

log = logging.getLogger(__name__)

MAX_URLS_PER_MESSAGE = 3  # Не пытаемся скачать 10 ссылок за раз


def build_router(owner_id: int, db: DB, brain: Brain,
                 system_prompt: str, memory_turns: int,
                 voice: Optional[VoiceTranscriber] = None,
                 vision: Optional[VisionAnalyzer] = None,
                 url_fetcher: Optional[URLFetcher] = None) -> Router:
    router = Router(name="messages")

    async def _respond_to(msg: Message, text: str,
                           extra_context: str = "") -> None:
        """Послать текст в brain, ответить. extra_context добавляется
        к системному промпту (например, для документа или URL).
        """
        await db.add_message(owner_id, "user", text)
        history = await db.last_messages(owner_id, memory_turns)
        if history and history[-1].get("role") == "user":
            history = history[:-1]
        await msg.bot.send_chat_action(msg.chat.id, "typing")
        system = system_prompt
        if extra_context:
            system = f"{system}\n\n---\n\n## Доп. контекст из этого сообщения\n\n{extra_context}"
        try:
            reply = await brain.chat(
                system=system, user=text, history=history,
                temperature=0.5, max_tokens=1500,
            )
        except Exception:
            log.exception("brain.chat failed")
            await msg.answer("DeepSeek подвис. Попробуй через минуту.")
            return
        await db.add_message(owner_id, "assistant", reply)
        for chunk in _split(reply, 3500):
            await msg.answer(chunk)

    # ---------- ТЕКСТ + URL ----------

    @router.message(F.text & ~F.text.startswith("/"))
    async def on_text(msg: Message) -> None:
        if msg.from_user is None or msg.from_user.id != owner_id:
            return
        text = msg.text or ""
        extra = ""

        # Если в тексте есть ссылки и парсер подключён — фетчим первые N
        urls = extract_urls(text)[:MAX_URLS_PER_MESSAGE] if url_fetcher else []
        if urls:
            await msg.bot.send_chat_action(msg.chat.id, "typing")
            try:
                pages = await fetch_many(url_fetcher, urls)
            except Exception:
                log.exception("URL fetch failed")
                pages = []
            chunks = []
            for p in pages:
                if p.error:
                    chunks.append(f"### {p.url}\n(не удалось загрузить: {p.error})")
                else:
                    head = f"### {p.title or p.url}\nURL: {p.url}"
                    chunks.append(f"{head}\n\n{p.text}")
            if chunks:
                extra = "Содержимое ссылок из сообщения:\n\n" + "\n\n---\n\n".join(chunks)

        await _respond_to(msg, text, extra_context=extra)

    # ---------- ГОЛОС ----------

    @router.message(F.voice)
    async def on_voice(msg: Message) -> None:
        if msg.from_user is None or msg.from_user.id != owner_id:
            return
        if voice is None:
            await msg.answer("Голосовые не подключены. Добавь GROQ_API_KEY.")
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
            await msg.answer("Не смог скачать голосовое.")
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
        await msg.answer(f"🎤 «{transcript}»")
        await _respond_to(msg, transcript)

    # ---------- ФОТО (с подписью или без) ----------

    @router.message(F.photo)
    async def on_photo(msg: Message) -> None:
        if msg.from_user is None or msg.from_user.id != owner_id:
            return
        if vision is None:
            await msg.answer("Анализ изображений не подключён. Добавь GROQ_API_KEY.")
            return
        if not msg.photo:
            return
        # Берём самое большое фото (последнее в массиве)
        photo = msg.photo[-1]
        caption = (msg.caption or "").strip()
        try:
            file = await msg.bot.get_file(photo.file_id)
            buf = io.BytesIO()
            await msg.bot.download_file(file.file_path, destination=buf)
            img_bytes = buf.getvalue()
        except Exception:
            log.exception("Photo download failed")
            await msg.answer("Не смог скачать изображение.")
            return
        await msg.bot.send_chat_action(msg.chat.id, "typing")
        try:
            description = await vision.describe(img_bytes, question=caption)
        except Exception:
            log.exception("Vision analyze failed")
            await msg.answer("Не получилось проанализировать изображение.")
            return
        # Готовим запрос для brain: caption (если есть) + описание изображения
        if caption:
            user_text = caption
            extra = f"К сообщению приложено изображение. Описание изображения:\n\n{description}"
        else:
            # Если подписи нет, просто отвечаем описанием и продолжаем диалог
            user_text = "Что на изображении? Прокомментируй."
            extra = f"Описание изображения:\n\n{description}"
        await _respond_to(msg, user_text, extra_context=extra)

    # ---------- ДОКУМЕНТЫ ----------

    @router.message(F.document)
    async def on_document(msg: Message) -> None:
        if msg.from_user is None or msg.from_user.id != owner_id:
            return
        if msg.document is None:
            return
        doc = msg.document
        caption = (msg.caption or "").strip()
        # Лимит 20 МБ через Bot API
        if doc.file_size and doc.file_size > 20 * 1024 * 1024:
            await msg.answer(
                f"Документ слишком большой ({doc.file_size // (1024*1024)} МБ). "
                "Лимит — 20 МБ."
            )
            return
        try:
            file = await msg.bot.get_file(doc.file_id)
            buf = io.BytesIO()
            await msg.bot.download_file(file.file_path, destination=buf)
            content = buf.getvalue()
        except Exception:
            log.exception("Document download failed")
            await msg.answer("Не смог скачать документ.")
            return
        parsed = parse_document(doc.file_name or "noname", content)
        if parsed.error:
            await msg.answer(f"Документ: {parsed.error}")
            return
        if not parsed.text.strip():
            await msg.answer("Документ распознался, но текст пустой.")
            return
        # Анонс с превью первых 200 символов
        preview = parsed.text[:200].replace("\n", " ")
        await msg.answer(
            f"📄 «{doc.file_name}» — {len(parsed.text)} символов.\n\n"
            f"Превью: {preview}..."
        )
        user_text = caption or f"Я прислал документ «{doc.file_name}». Разбери и прокомментируй."
        extra = f"Содержимое документа «{doc.file_name}»:\n\n{parsed.text}"
        await _respond_to(msg, user_text, extra_context=extra)

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
