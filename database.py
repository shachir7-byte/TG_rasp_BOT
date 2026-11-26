from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import aiosqlite

from config import DB_FILE, PRIMARY_ENGINE, TIMEZONE

_db_connection: aiosqlite.Connection | None = None
_db_lock = asyncio.Lock()


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return {key: row[key] for key in row.keys()}


async def get_db_connection() -> aiosqlite.Connection:
    global _db_connection
    if _db_connection is None:
        async with _db_lock:
            if _db_connection is None:
                connection = await aiosqlite.connect(DB_FILE)
                connection.row_factory = aiosqlite.Row
                await connection.execute("PRAGMA journal_mode=WAL")
                await connection.execute("PRAGMA foreign_keys=ON")
                _db_connection = connection
    return _db_connection


async def close_db_connection() -> None:
    global _db_connection
    if _db_connection is not None:
        connection, _db_connection = _db_connection, None
        await connection.close()


async def init_db() -> None:
    db = await get_db_connection()

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            user_group TEXT,
            notify_time TEXT,
            notify_type TEXT DEFAULT 'time',
            notify_offset INTEGER DEFAULT 0,
            request_count INTEGER DEFAULT 0,
            last_sent_date TEXT,
            first_seen TEXT
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            action TEXT,
            timestamp TEXT
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    await db.execute("DROP TABLE IF EXISTS groups_config")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_user_group ON users(user_group)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_stats_chat ON stats(chat_id)")

    await _migrate(db)
    await db.commit()


async def _migrate(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA user_version") as cursor:
        row = await cursor.fetchone()
        version = row[0] if row else 0

    if version < 1:
        await db.execute("UPDATE users SET user_group = 'ИС-23/9-3' WHERE user_group = 'is'")
        await db.execute("UPDATE users SET user_group = 'Д-23/9-1' WHERE user_group = 'd'")
        await db.execute("PRAGMA user_version = 1")


async def save_user(chat_id: int, username: str | None = None, group: str | None = None) -> None:
    db = await get_db_connection()
    now = datetime.now(TIMEZONE).isoformat()
    await db.execute(
        "INSERT OR IGNORE INTO users (chat_id, username, user_group, first_seen, request_count) "
        "VALUES (?, ?, ?, ?, 0)",
        (chat_id, username, group, now),
    )
    if username is not None:
        await db.execute("UPDATE users SET username = ? WHERE chat_id = ?", (username, chat_id))
    if group is not None:
        await db.execute("UPDATE users SET user_group = ? WHERE chat_id = ?", (group, chat_id))
    await db.commit()


async def get_user_group(chat_id: int) -> str | None:
    db = await get_db_connection()
    async with db.execute("SELECT user_group FROM users WHERE chat_id = ?", (chat_id,)) as cursor:
        row = await cursor.fetchone()
        return row["user_group"] if row else None


async def set_user_group(chat_id: int, group: str) -> None:
    db = await get_db_connection()
    await db.execute("UPDATE users SET user_group = ? WHERE chat_id = ?", (group, chat_id))
    await db.commit()


async def get_user_settings(chat_id: int) -> dict | None:
    db = await get_db_connection()
    async with db.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)) as cursor:
        row = await cursor.fetchone()
        return _row_to_dict(row) if row else None


async def update_notify_settings(
    chat_id: int,
    time: str | None = None,
    notify_type: str = "time",
    offset: int = 0,
) -> None:
    db = await get_db_connection()
    await db.execute(
        "UPDATE users SET notify_time = ?, notify_type = ?, notify_offset = ? WHERE chat_id = ?",
        (time, notify_type, offset, chat_id),
    )
    await db.commit()


async def cancel_notify(chat_id: int) -> None:
    db = await get_db_connection()
    await db.execute(
        "UPDATE users SET notify_time = NULL, notify_type = 'time', notify_offset = 0 WHERE chat_id = ?",
        (chat_id,),
    )
    await db.commit()


async def increment_request_count(chat_id: int) -> None:
    db = await get_db_connection()
    await db.execute(
        "UPDATE users SET request_count = request_count + 1 WHERE chat_id = ?", (chat_id,)
    )
    await db.commit()


async def update_last_sent(chat_id: int, date_str: str) -> None:
    db = await get_db_connection()
    await db.execute("UPDATE users SET last_sent_date = ? WHERE chat_id = ?", (date_str, chat_id))
    await db.commit()


async def get_active_users_for_worker():
    db = await get_db_connection()
    async with db.execute(
        """
        SELECT chat_id, user_group, notify_time, notify_type, notify_offset, last_sent_date
        FROM users
        WHERE user_group IS NOT NULL
          AND (notify_time IS NOT NULL OR (notify_type = 'offset' AND notify_offset > 0))
        """
    ) as cursor:
        return [dict(row) for row in await cursor.fetchall()]


async def get_all_users():
    db = await get_db_connection()
    async with db.execute("SELECT chat_id, username, user_group FROM users") as cursor:
        return [dict(row) for row in await cursor.fetchall()]


async def get_all_users_chat_ids() -> list[int]:
    db = await get_db_connection()
    async with db.execute("SELECT chat_id FROM users") as cursor:
        return [row["chat_id"] for row in await cursor.fetchall()]


async def get_user_count() -> int:
    db = await get_db_connection()
    async with db.execute("SELECT COUNT(*) AS count FROM users") as cursor:
        row = await cursor.fetchone()
        return row["count"] if row else 0


async def log_action(chat_id: int, action: str) -> None:
    db = await get_db_connection()
    await db.execute(
        "INSERT INTO stats (chat_id, action, timestamp) VALUES (?, ?, ?)",
        (chat_id, action, datetime.now(TIMEZONE).isoformat()),
    )
    await db.commit()


async def get_admin_stats() -> list[dict]:
    db = await get_db_connection()
    async with db.execute(
        """
        SELECT u.chat_id, u.username, u.user_group, u.request_count, u.first_seen,
               (SELECT s.action FROM stats s WHERE s.chat_id = u.chat_id
                ORDER BY s.timestamp DESC LIMIT 1) AS last_action
        FROM users u
        ORDER BY u.request_count DESC
        """
    ) as cursor:
        return [dict(row) for row in await cursor.fetchall()]


async def clear_stats(days: int = 30) -> int:
    """Удалить записи статистики старше ``days`` дней (по умолчанию 30)."""
    db = await get_db_connection()
    cutoff = (datetime.now(TIMEZONE) - timedelta(days=days)).isoformat()
    cursor = await db.execute("DELETE FROM stats WHERE timestamp < ?", (cutoff,))
    deleted = cursor.rowcount
    await cursor.close()
    await db.commit()
    return deleted


async def get_config(key: str, default: str | None = None) -> str | None:
    db = await get_db_connection()
    async with db.execute("SELECT value FROM app_config WHERE key = ?", (key,)) as cursor:
        row = await cursor.fetchone()
        return row["value"] if row else default


async def set_config(key: str, value: str) -> None:
    db = await get_db_connection()
    await db.execute(
        "INSERT INTO app_config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    await db.commit()


async def get_selected_engine() -> str:
    value = await get_config("engine")
    return value or PRIMARY_ENGINE


async def set_selected_engine(engine_key: str) -> None:
    await set_config("engine", engine_key)
