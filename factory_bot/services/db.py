"""SQLite-память + контент-фабрика.

Таблицы:
- messages           — история диалога (для контекста)
- statuses           — задачи Сергея (что снято, отправлено, перенесено)
- settings           — паузы
- competitor_channels — каналы конкурентов
- cases              — кейсы клиентов Сергея
- content_pieces     — единицы контента (драфты, одобренные, опубликованные)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

import aiosqlite

log = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role    TEXT NOT NULL,
    content TEXT NOT NULL,
    ts      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_user_ts ON messages(user_id, ts);

CREATE TABLE IF NOT EXISTS statuses (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL,
    label    TEXT NOT NULL,
    status   TEXT NOT NULL,
    note     TEXT,
    ts       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    user_id      INTEGER PRIMARY KEY,
    paused_until INTEGER,
    data         TEXT
);

CREATE TABLE IF NOT EXISTS competitor_channels (
    username  TEXT PRIMARY KEY,
    title     TEXT,
    note      TEXT,
    last_scan INTEGER,
    active    INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS cases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    niche      TEXT,
    background TEXT,
    result     TEXT,
    quotes     TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS content_pieces (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    type       TEXT NOT NULL,
    status     TEXT NOT NULL,
    title      TEXT,
    body       TEXT,
    extra_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pieces_status ON content_pieces(status);
"""


def _now() -> int:
    return int(time.time())


class DB:
    def __init__(self, path: Path):
        self.path = path

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()
        log.info("DB initialized at %s", self.path)

    # ---------- сообщения ----------

    async def add_message(self, user_id: int, role: str, content: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO messages(user_id, role, content, ts) VALUES (?, ?, ?, ?)",
                (user_id, role, content, _now()),
            )
            await db.commit()

    async def last_messages(self, user_id: int, limit: int) -> List[dict]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT role, content FROM messages "
                "WHERE user_id = ? ORDER BY ts DESC LIMIT ?",
                (user_id, limit),
            )
            rows = await cur.fetchall()
        rows.reverse()
        return [{"role": r, "content": c} for r, c in rows]

    # ---------- статусы ----------

    async def add_status(self, user_id: int, label: str, status: str,
                         note: Optional[str] = None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO statuses(user_id, label, status, note, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, label, status, note, _now()),
            )
            await db.commit()

    async def recent_statuses(self, user_id: int, limit: int = 30) -> List[dict]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT label, status, note, ts FROM statuses "
                "WHERE user_id = ? ORDER BY ts DESC LIMIT ?",
                (user_id, limit),
            )
            rows = await cur.fetchall()
        return [
            {"label": r[0], "status": r[1], "note": r[2],
             "ts_iso": datetime.fromtimestamp(r[3]).isoformat(timespec="seconds")}
            for r in rows
        ]

    # ---------- настройки (паузы) ----------

    async def set_pause(self, user_id: int, until_ts: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO settings(user_id, paused_until) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET paused_until = excluded.paused_until",
                (user_id, until_ts),
            )
            await db.commit()

    async def is_paused(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT paused_until FROM settings WHERE user_id = ?", (user_id,)
            )
            row = await cur.fetchone()
        if not row or not row[0]:
            return False
        return int(row[0]) > _now()

    # ---------- competitor_channels ----------

    async def channel_upsert(self, username: str, title: str = "", note: str = "") -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO competitor_channels(username, title, note, active) "
                "VALUES (?, ?, ?, 1) "
                "ON CONFLICT(username) DO UPDATE SET title = excluded.title, "
                "note = excluded.note, active = 1",
                (username, title, note),
            )
            await db.commit()

    async def channels_active(self) -> List[dict]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT username, title, last_scan FROM competitor_channels "
                "WHERE active = 1 ORDER BY username"
            )
            rows = await cur.fetchall()
        return [{"username": r[0], "title": r[1], "last_scan": r[2]} for r in rows]

    async def channel_mark_scanned(self, username: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE competitor_channels SET last_scan = ? WHERE username = ?",
                (_now(), username),
            )
            await db.commit()

    # ---------- cases ----------

    async def case_add(self, name: str, niche: str, background: str,
                       result: str, quotes: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO cases(name, niche, background, result, quotes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, niche, background, result, quotes, _now()),
            )
            await db.commit()
            return cur.lastrowid

    async def cases_all(self) -> List[dict]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT id, name, niche, background, result, quotes FROM cases "
                "ORDER BY created_at"
            )
            rows = await cur.fetchall()
        return [
            {"id": r[0], "name": r[1], "niche": r[2], "background": r[3],
             "result": r[4], "quotes": r[5]}
            for r in rows
        ]

    # ---------- content_pieces ----------

    async def piece_save(self, type_: str, title: str, body: str,
                         extra: Optional[dict] = None,
                         status: str = "draft") -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO content_pieces(type, status, title, body, extra_json, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (type_, status, title, body,
                 json.dumps(extra or {}, ensure_ascii=False), _now(), _now()),
            )
            await db.commit()
            return cur.lastrowid

    async def pieces_by_status(self, status: str, limit: int = 30) -> List[dict]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT id, type, status, title, body, extra_json FROM content_pieces "
                "WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
            rows = await cur.fetchall()
        return [
            {"id": r[0], "type": r[1], "status": r[2], "title": r[3],
             "body": r[4], "extra": json.loads(r[5] or "{}")}
            for r in rows
        ]

    async def piece_get(self, piece_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT id, type, status, title, body, extra_json "
                "FROM content_pieces WHERE id = ?",
                (piece_id,),
            )
            r = await cur.fetchone()
        if not r:
            return None
        return {"id": r[0], "type": r[1], "status": r[2], "title": r[3],
                "body": r[4], "extra": json.loads(r[5] or "{}")}

    async def piece_set_status(self, piece_id: int, status: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE content_pieces SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), piece_id),
            )
            await db.commit()
