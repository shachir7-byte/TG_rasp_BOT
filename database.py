import aiosqlite
from datetime import datetime, timezone, timedelta
from config import LOCAL_TIMEZONE_OFFSET, DEFAULT_GROUP, GROUPS_CONFIG

DB_FILE = "schedule.db"
TZ = timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET))

# Глобальное соединение
_db_connection: aiosqlite.Connection = None

async def get_db_connection() -> aiosqlite.Connection:
    global _db_connection
    if _db_connection is None:
        _db_connection = await aiosqlite.connect(DB_FILE)
        # Включаем WAL режим для лучшей производительности
        await _db_connection.execute("PRAGMA journal_mode=WAL")
    return _db_connection

async def close_db_connection():
    global _db_connection
    if _db_connection:
        await _db_connection.close()
        _db_connection = None

async def init_db():
    db = await get_db_connection()
    
    # Таблица пользователей
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            user_group TEXT DEFAULT 'is',
            notify_time TEXT,
            notify_type TEXT DEFAULT 'time',
            notify_offset INTEGER DEFAULT 0,
            notify_restart INTEGER DEFAULT 0,
            request_count INTEGER DEFAULT 0,
            last_sent_date TEXT,
            first_seen TEXT
        )
    """)
    
    # Таблица статистики
    await db.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            action TEXT,
            timestamp TEXT
        )
    """)
    
    # Таблица конфигурации групп
    await db.execute("""
        CREATE TABLE IF NOT EXISTS groups_config (
            key TEXT PRIMARY KEY,
            name TEXT,
            group_id TEXT
        )
    """)

    # Индексы
    await db.execute("CREATE INDEX IF NOT EXISTS idx_user_group ON users(user_group)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_stats_chat ON stats(chat_id)")

    # Заполнение групп
    for key, data in GROUPS_CONFIG.items():
        await db.execute(
            "INSERT OR IGNORE INTO groups_config (key, name, group_id) VALUES (?, ?, ?)",
            (key, data['name'], data['id'])
        )
    
    await db.commit()

# --- Хелперы ---

async def save_user(chat_id: int, username: str = None, group: str = DEFAULT_GROUP):
    db = await get_db_connection()
    now = datetime.now(TZ).isoformat()
    await db.execute(
        "INSERT OR IGNORE INTO users (chat_id, username, user_group, first_seen, request_count) VALUES (?, ?, ?, ?, 0)",
        (chat_id, username, group, now)
    )
    await db.execute(
        "UPDATE users SET username = COALESCE(?, username), user_group = COALESCE(?, user_group) WHERE chat_id = ?",
        (username, group, chat_id)
    )
    await db.commit()

async def get_user_group(chat_id: int) -> str:
    db = await get_db_connection()
    async with db.execute("SELECT user_group FROM users WHERE chat_id = ?", (chat_id,)) as cursor:
        row = await cursor.fetchone()
        return row[0] if row else DEFAULT_GROUP

async def get_user_settings(chat_id: int):
    db = await get_db_connection()
    async with db.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)) as cursor:
        return await cursor.fetchone()

async def update_notify_settings(chat_id: int, time: str = None, n_type: str = 'time', offset: int = 0, restart: int = 0):
    db = await get_db_connection()
    await db.execute(
        "UPDATE users SET notify_time = ?, notify_type = ?, notify_offset = ?, notify_restart = ? WHERE chat_id = ?",
        (time, n_type, offset, restart, chat_id)
    )
    await db.commit()

async def cancel_notify(chat_id: int):
    db = await get_db_connection()
    await db.execute(
        "UPDATE users SET notify_time = NULL, notify_type = 'time', notify_offset = 0 WHERE chat_id = ?",
        (chat_id,)
    )
    await db.commit()

async def increment_request_count(chat_id: int):
    db = await get_db_connection()
    await db.execute("UPDATE users SET request_count = request_count + 1 WHERE chat_id = ?", (chat_id,))
    await db.commit()

async def update_last_sent(chat_id: int, date_str: str):
    db = await get_db_connection()
    await db.execute("UPDATE users SET last_sent_date = ? WHERE chat_id = ?", (date_str, chat_id))
    await db.commit()

async def get_active_users_for_worker():
    db = await get_db_connection()
    async with db.execute("""
        SELECT chat_id, notify_time, notify_type, notify_offset, last_sent_date, user_group 
        FROM users 
        WHERE notify_time IS NOT NULL OR (notify_type = 'offset' AND notify_offset > 0)
    """) as cursor:
        return await cursor.fetchall()

async def get_all_users():
    db = await get_db_connection()
    async with db.execute("SELECT chat_id, username, user_group FROM users") as cursor:
        return await cursor.fetchall()

async def get_user_count() -> int:
    db = await get_db_connection()
    async with db.execute("SELECT COUNT(*) FROM users") as cursor:
        row = await cursor.fetchone()
        return row[0] if row else 0

async def get_groups_config():
    db = await get_db_connection()
    async with db.execute("SELECT key, name, group_id FROM groups_config") as cursor:
        rows = await cursor.fetchall()
        return {r[0]: {"name": r[1], "group_id": r[2]} for r in rows}

async def log_action(chat_id: int, action: str):
    db = await get_db_connection()
    await db.execute(
        "INSERT INTO stats (chat_id, action, timestamp) VALUES (?, ?, ?)",
        (chat_id, action, datetime.now(TZ).isoformat())
    )
    await db.commit()

async def get_full_admin_stats():
    db = await get_db_connection()
    groups = await get_groups_config()
    async with db.execute("""
        SELECT u.chat_id, u.username, u.user_group, u.request_count, u.first_seen,
               (SELECT s.action FROM stats s WHERE s.chat_id = u.chat_id ORDER BY s.timestamp DESC LIMIT 1) as last_act
        FROM users u ORDER BY u.request_count DESC
    """) as cursor:
        users = await cursor.fetchall()

    total_users = len(users)
    total_requests = sum(u[3] for u in users)
    lines = [f"📊 <b>Полная статистика</b>\n\n👥 Всего: <b>{total_users}</b>", f"📈 Запросов: <b>{total_requests}</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"]

    for chat_id, username, grp, count, first_seen, last_act in users:
        name = f"@{username}" if username else f"User {chat_id}"
        grp_name = groups.get(grp, {}).get('name', grp)
        act_info = f"<i>{last_act}</i>" if last_act else "—"
        lines.append(f"🆔 <code>{chat_id}</code> | <b>{name}</b> ({grp_name})\n   └ Запросов: {count} | Активность: {act_info}\n")

    return "\n".join(lines)

async def clear_stats():
    db = await get_db_connection()
    await db.execute("DELETE FROM stats")
    deleted = db.total_changes
    await db.commit()
    return deleted