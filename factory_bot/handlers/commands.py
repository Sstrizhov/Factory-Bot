"""Slash-команды бота. Доступ только владельцу (OWNER_TG_ID)."""
from __future__ import annotations

import logging
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from factory_bot.services.db import DB
from factory_bot.services.factory import Factory

log = logging.getLogger(__name__)


HELP_TEXT = (
    "На связи. Я — твой ассистент и контент-фабрика.\n\n"
    "Команды:\n"
    "/pack [тема] — собрать пакет (5 Reels + 5 ТГ-постов + 3 карусели)\n"
    "/drafts — список черновиков\n"
    "/show [id] — показать полный текст драфта\n"
    "/approve [id] — одобрить драфт\n"
    "/channel [username] — добавить канал конкурента\n"
    "/channels — список каналов\n"
    "/case — добавить кейс клиента (5 шагов)\n"
    "/cases — список кейсов\n"
    "/done [метка] — отметить, что сделано\n"
    "/pause [часов] — приглушить плановые сообщения\n"
    "/resume — снять паузу\n\n"
    "Можешь писать свободным текстом — отвечу как ассистент.\n"
    "Расписание: пн 9:00 МСК — авто-сборка пакета на неделю."
)


class CaseStates(StatesGroup):
    name = State()
    niche = State()
    background = State()
    result = State()
    quotes = State()


def build_router(owner_id: int, db: DB, factory: Factory) -> Router:
    router = Router(name="commands")

    def is_owner(msg: Message) -> bool:
        return msg.from_user is not None and msg.from_user.id == owner_id

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def cmd_start(msg: Message, state: FSMContext) -> None:
        if not is_owner(msg):
            await msg.answer("Этот бот личный."); return
        await state.clear()
        await msg.answer(HELP_TEXT)

    # ---------- /pack ----------

    @router.message(Command("pack"))
    async def cmd_pack(msg: Message) -> None:
        if not is_owner(msg): return
        topic = (msg.text or "").partition(" ")[2].strip() or None
        await msg.bot.send_chat_action(msg.chat.id, "typing")
        await msg.answer("Собираю пакет — 30-90 сек, не торопи. Анализ конкурентов + кейсы + сценарии.")
        try:
            result = await factory.pack(topic)
        except Exception as exc:
            log.exception("/pack failed")
            await msg.answer(f"Не получилось: {exc}"); return
        for chunk in _split(result["summary"], 3500):
            await msg.answer(chunk)

    # ---------- /drafts /show /approve ----------

    @router.message(Command("drafts"))
    async def cmd_drafts(msg: Message) -> None:
        if not is_owner(msg): return
        drafts = await db.pieces_by_status("draft", limit=50)
        if not drafts:
            await msg.answer("Драфтов нет. Запусти /pack."); return
        type_short = {"reels_script": "Reels", "tg_post": "ТГ", "carousel_brief": "Карусель"}
        lines = [f"Драфтов: {len(drafts)}", ""]
        for d in drafts:
            ts = type_short.get(d["type"], d["type"])
            lines.append(f"  #{d['id']} [{ts}] {d['title']}")
        lines.append("")
        lines.append("/show N — полный текст. /approve N — одобрить.")
        await msg.answer("\n".join(lines))

    @router.message(Command("show"))
    async def cmd_show(msg: Message) -> None:
        if not is_owner(msg): return
        arg = (msg.text or "").partition(" ")[2].strip().lstrip("#")
        try:
            pid = int(arg)
        except ValueError:
            await msg.answer("Формат: /show 17"); return
        piece = await db.piece_get(pid)
        if not piece:
            await msg.answer(f"Драфт #{pid} не найден."); return
        type_short = {"reels_script": "Reels", "tg_post": "ТГ-пост", "carousel_brief": "Карусель"}
        ts = type_short.get(piece["type"], piece["type"])
        text = (
            f"#{piece['id']} [{ts}] · {piece['status']}\n"
            f"Заголовок: {piece['title']}\n\n"
            f"{piece['body']}"
        )
        for chunk in _split(text, 3500):
            await msg.answer(chunk)

    @router.message(Command("approve"))
    async def cmd_approve(msg: Message) -> None:
        if not is_owner(msg): return
        arg = (msg.text or "").partition(" ")[2].strip().lstrip("#")
        try:
            pid = int(arg)
        except ValueError:
            await msg.answer("Формат: /approve 17"); return
        await db.piece_set_status(pid, "approved")
        await msg.answer(f"Драфт #{pid} → approved.")

    # ---------- /channel /channels ----------

    @router.message(Command("channel"))
    async def cmd_channel(msg: Message) -> None:
        if not is_owner(msg): return
        arg = (msg.text or "").partition(" ")[2].strip().lstrip("@")
        if not arg:
            await msg.answer("Формат: /channel salesandsex"); return
        await db.channel_upsert(arg, title=arg)
        await msg.answer(f"Канал @{arg} добавлен в мониторинг.")

    @router.message(Command("channels"))
    async def cmd_channels(msg: Message) -> None:
        if not is_owner(msg): return
        channels = await db.channels_active()
        if not channels:
            await msg.answer("Каналов нет. Добавь через /channel."); return
        lines = [f"Каналов: {len(channels)}"]
        for c in channels:
            scan = "—" if not c["last_scan"] else datetime.fromtimestamp(c["last_scan"]).strftime("%d.%m")
            lines.append(f"  @{c['username']} · скан: {scan}")
        await msg.answer("\n".join(lines))

    # ---------- /case (FSM) ----------

    @router.message(Command("case"))
    async def case_start(msg: Message, state: FSMContext) -> None:
        if not is_owner(msg): return
        await state.set_state(CaseStates.name)
        await msg.answer("Кейс клиента. Имя?")

    @router.message(CaseStates.name, F.text)
    async def case_name(msg: Message, state: FSMContext) -> None:
        await state.update_data(name=(msg.text or "").strip())
        await state.set_state(CaseStates.niche)
        await msg.answer("Ниша / профессия?")

    @router.message(CaseStates.niche, F.text)
    async def case_niche(msg: Message, state: FSMContext) -> None:
        await state.update_data(niche=(msg.text or "").strip())
        await state.set_state(CaseStates.background)
        await msg.answer("Точка А (что было)?")

    @router.message(CaseStates.background, F.text)
    async def case_background(msg: Message, state: FSMContext) -> None:
        await state.update_data(background=(msg.text or "").strip())
        await state.set_state(CaseStates.result)
        await msg.answer("Точка Б (что изменилось)?")

    @router.message(CaseStates.result, F.text)
    async def case_result(msg: Message, state: FSMContext) -> None:
        await state.update_data(result=(msg.text or "").strip())
        await state.set_state(CaseStates.quotes)
        await msg.answer("Цитаты или формулировки клиента?")

    @router.message(CaseStates.quotes, F.text)
    async def case_quotes(msg: Message, state: FSMContext) -> None:
        data = await state.get_data()
        cid = await db.case_add(
            name=data["name"], niche=data.get("niche", ""),
            background=data.get("background", ""), result=data.get("result", ""),
            quotes=(msg.text or "").strip(),
        )
        await state.clear()
        await msg.answer(f"Кейс #{cid} «{data['name']}» сохранён.")

    @router.message(Command("cases"))
    async def cmd_cases(msg: Message) -> None:
        if not is_owner(msg): return
        cases = await db.cases_all()
        if not cases:
            await msg.answer("Кейсов нет. Добавь через /case."); return
        lines = [f"Кейсов: {len(cases)}"]
        for c in cases:
            lines.append(f"  #{c['id']} {c['name']} · {c.get('niche') or '—'}")
        await msg.answer("\n".join(lines))

    # ---------- /done /pause /resume ----------

    @router.message(Command("done"))
    async def cmd_done(msg: Message) -> None:
        if not is_owner(msg): return
        text = (msg.text or "").partition(" ")[2].strip()
        if not text:
            await msg.answer("Укажи метку: /done Reel #3"); return
        await db.add_status(owner_id, label=text, status="done")
        await msg.answer(f"Зафиксировал: {text}.")

    @router.message(Command("pause"))
    async def cmd_pause(msg: Message) -> None:
        if not is_owner(msg): return
        arg = (msg.text or "").partition(" ")[2].strip()
        try:
            hours = int(arg) if arg else 24
        except ValueError:
            await msg.answer("Формат: /pause 6"); return
        until = int(time.time()) + hours * 3600
        await db.set_pause(owner_id, until)
        await msg.answer(f"Паузу до {datetime.fromtimestamp(until).strftime('%d.%m %H:%M')}.")

    @router.message(Command("resume"))
    async def cmd_resume(msg: Message) -> None:
        if not is_owner(msg): return
        await db.set_pause(owner_id, 0)
        await msg.answer("Паузу снял.")

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
