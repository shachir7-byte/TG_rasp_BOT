import aiosqlite
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple, Dict, Any

from config import LOCAL_TIMEZONE_OFFSET, DEFAULT_GROUP, GROUPS_CONFIG

logger = logging.getLogger(__name__)

DB_PATH = "schedule.db"
LOCAL_TZ = timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET))

# Глобальное соединение с БД (открывается один раз при старте бота для скорости)
_db_conn: Optional[aiosqlite.Connection] = None

async def get_db() -> aiosqlite.Connection:
    """Возвращает активное соединение с БД или создает новое с оптимизацией WAL."""
    global _db_conn
    if _db_conn is None:
        _db_conn = await aiosqlite.connect(DB_PATH)
        # Включаем WAL-режим: запись и чтение не блокируют друг друга и не нагружают диск
        await _db_conn.execute("PRAGMA journal_mode=WAL;")
        await _db_conn.execute("PRAGMA synchronous=NORMAL;")
    return _db_conn

async def init_db():
    """
    Инициализация таблиц базы данных и автоматическая миграция старой схемы.
    Гарантирует наличие всех нужных колонок без потери существующих пользователей.
    """
    db = await get_db()
    
    # 1. Базовое создание таблицы пользователей (если файла БД еще не было)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            group_key TEXT DEFAULT 'is',
            notify_time TEXT DEFAULT NULL,
            notify_type TEXT DEFAULT 'none',
            notify_offset INTEGER DEFAULT 0,
            notify_updates INTEGER DEFAULT 1,
            last_sent TEXT DEFAULT NULL,
            requests_count INTEGER DEFAULT 0
        )
    """)
    
    # 2. АВТО-МИГРАЦИЯ: проверяем реальные колонки в существующей таблице users
    async with db.execute("PRAGMA table_info(users);") as cursor:
        columns_info = await cursor.fetchall()
        existing_columns = {col[1] for col in columns_info}

    # Список всех необходимых колонок и их определений
    required_columns = {
        "username": "TEXT",
        "group_key": f"TEXT DEFAULT '{DEFAULT_GROUP}'",
        "notify_time": "TEXT DEFAULT NULL",
        "notify_type": "TEXT DEFAULT 'none'",
        "notify_offset": "INTEGER DEFAULT 0",
        "notify_updates": "INTEGER DEFAULT 1",
        "last_sent": "TEXT DEFAULT NULL",
        "requests_count": "INTEGER DEFAULT 0"
    }

    # Докидываем недостающие колонки на лету
    for col_name, col_def in required_columns.items():
        if col_name not in existing_columns:
            logger.info(f"🛠 Авто-миграция БД: добавляю недостающую колонку '{col_name}' в таблицу users")
            await db.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def};")

    # Если в старой БД колонка группы называлась 'user_group' или 'group_id', переносим данные в 'group_key'
    if "user_group" in existing_columns:
        await db.execute("UPDATE users SET group_key = user_group WHERE group_key IS NULL OR group_key = '';")
    elif "group_id" in existing_columns:
        await db.execute("UPDATE users SET group_key = group_id WHERE group_key IS NULL OR group_key = '';")

    # Если в старой БД счетчик назывался 'request_count', переносим данные в 'requests_count'
    if "request_count" in existing_columns:
        await db.execute("UPDATE users SET requests_count = request_count WHERE requests_count = 0 AND request_count > 0;")

    # 3. Таблица логов действий для статистики
    await db.execute("""
        CREATE TABLE IF NOT EXISTS action_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            action TEXT,
            timestamp TEXT
        )
    """)
    
    # Проверяем наличие колонок в action_logs на случай старой структуры
    async with db.execute("PRAGMA table_info(action_logs);") as cursor:
        log_cols = {col[1] for col in await cursor.fetchall()}
    if "timestamp" not in log_cols:
        await db.execute("ALTER TABLE action_logs ADD COLUMN timestamp TEXT;")

    await db.commit()
    logger.info("Database initialized and verified (WAL mode enabled)")

async def close_db_connection():
    """Корректное закрытие соединения с БД при остановке бота."""
    global _db_conn
    if _db_conn is not None:
        await _db_conn.close()
        _db_conn = None
        logger.info("Database connection closed")

async def get_groups_config() -> Dict[str, Any]:
    """Возвращает словарь конфигурации групп."""
    return GROUPS_CONFIG

async def save_user(chat_id: int, username: Optional[str], group_key: str = DEFAULT_GROUP):
    """Сохраняет нового пользователя или обновляет его группу и username."""
    db = await get_db()
    uname = username or "unknown"
    await db.execute("""
        INSERT INTO users (chat_id, username, group_key)
        VALUES (?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            username = excluded.username,
            group_key = excluded.group_key
    """, (chat_id, uname, group_key))
    await db.commit()

async def get_user_group(chat_id: int) -> str:
    """Получает ключ группы пользователя (по умолчанию 'is')."""
    db = await get_db()
    async with db.execute("SELECT group_key FROM users WHERE chat_id = ?", (chat_id,)) as cursor:
        row = await cursor.fetchone()
        if row and row[0] in GROUPS_CONFIG:
            return row[0]
    return DEFAULT_GROUP

async def get_user_settings(chat_id: int) -> Optional[Tuple]:
    """
    Возвращает кортеж настроек пользователя:
    (chat_id, username, group_key, notify_time, notify_type, notify_offset, notify_updates, last_sent, requests_count)
    """
    db = await get_db()
    async with db.execute("""
        SELECT chat_id, username, group_key, notify_time, notify_type, notify_offset, notify_updates, last_sent, requests_count
        FROM users WHERE chat_id = ?
    """, (chat_id,)) as cursor:
        return await cursor.fetchone()

async def update_notify_settings(chat_id: int, time: Optional[str], n_type: str, offset: int):
    """Обновляет настройки уведомлений пользователя и сбрасывает маркер last_sent."""
    db = await get_db()
    await db.execute("""
        UPDATE users
        SET notify_time = ?, notify_type = ?, notify_offset = ?, last_sent = NULL
        WHERE chat_id = ?
    """, (time, n_type, offset, chat_id))
    await db.commit()

async def cancel_notify(chat_id: int):
    """Отключает уведомления для пользователя."""
    db = await get_db()
    await db.execute("""
        UPDATE users
        SET notify_time = NULL, notify_type = 'none', notify_offset = 0
        WHERE chat_id = ?
    """, (chat_id,))
    await db.commit()

async def increment_request_count(chat_id: int):
    """Увеличивает счетчик запросов расписания пользователя."""
    db = await get_db()
    await db.execute("""
        UPDATE users SET requests_count = requests_count + 1 WHERE chat_id = ?
    """, (chat_id,))
    await db.commit()

async def log_action(chat_id: int, action: str):
    """Записывает действие пользователя в таблицу статистики."""
    db = await get_db()
    now_str = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute("""
        INSERT INTO action_logs (chat_id, action, timestamp)
        VALUES (?, ?, ?)
    """, (chat_id, action, now_str))
    await db.commit()

async def get_all_users() -> List[Tuple]:
    """Возвращает список всех пользователей из БД."""
    db = await get_db()
    async with db.execute("SELECT chat_id, username, group_key, requests_count FROM users") as cursor:
        return await cursor.fetchall()

async def get_all_users_chat_ids() -> List[int]:
    """Возвращает список только chat_id всех пользователей (для рассылок)."""
    db = await get_db()
    async with db.execute("SELECT chat_id FROM users") as cursor:
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

async def get_user_count() -> int:
    """Возвращает общее количество пользователей."""
    db = await get_db()
    async with db.execute("SELECT COUNT(*) FROM users") as cursor:
        row = await cursor.fetchone()
        return row[0] if row else 0

async def get_active_users_for_worker() -> List[Tuple]:
    """Возвращает пользователей с включенными уведомлениями для фонового воркера."""
    db = await get_db()
    async with db.execute("""
        SELECT chat_id, notify_time, notify_type, notify_offset, last_sent, group_key
        FROM users
        WHERE notify_type IN ('time', 'offset')
    """) as cursor:
        return await cursor.fetchall()

async def update_last_sent(chat_id: int, date_str: str):
    """Отмечает дату последней отправки уведомления, чтобы не слать дубли в один день."""
    db = await get_db()
    await db.execute("UPDATE users SET last_sent = ? WHERE chat_id = ?", (date_str, chat_id))
    await db.commit()

async def clear_stats():
    """Очищает старые логи действий (вызывается воркером раз в сутки для экономии места)."""
    db = await get_db()
    await db.execute("DELETE FROM action_logs")
    await db.commit()

async def get_logs(limit: int = 2000) -> List[Tuple]:
    """Возвращает последние записи логов."""
    db = await get_db()
    async with db.execute("SELECT id, chat_id, action, timestamp FROM action_logs ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
        return await cursor.fetchall()

async def get_full_admin_stats() -> str:
    """Формирует полный текстовый отчет со статистикой для админ-панели."""
    db = await get_db()
    
    # 1. Количество пользователей и распределение по группам
    async with db.execute("SELECT group_key, COUNT(*) FROM users GROUP BY group_key") as cursor:
        group_rows = await cursor.fetchall()
        
    total_users = sum(r[1] for r in group_rows)
    
    # 2. Количество активных подписок на уведомления
    async with db.execute("SELECT COUNT(*) FROM users WHERE notify_type != 'none'") as cursor:
        notify_row = await cursor.fetchone()
        notify_count = notify_row[0] if notify_row else 0

    # 3. Топ-5 активных пользователей
    async with db.execute("SELECT username, group_key, requests_count FROM users ORDER BY requests_count DESC LIMIT 5") as cursor:
        top_users = await cursor.fetchall()

    # 4. Топ-5 популярных кнопок за сегодня
    async with db.execute("SELECT action, COUNT(*) as cnt FROM action_logs GROUP BY action ORDER BY cnt DESC LIMIT 5") as cursor:
        top_actions = await cursor.fetchall()

    lines = [
        "📊 <b>Статистика бота</b>",
        "──────────────────────────",
        f"👥 <b>Всего пользователей:</b> <code>{total_users}</code>",
        f"🔔 <b>С уведомлениями:</b> <code>{notify_count}</code>\n",
        "📚 <b>По группам:</b>"
    ]
    
    for g_key, cnt in group_rows:
        g_name = GROUPS_CONFIG.get(g_key, {}).get('name', g_key)
        lines.append(f" • {g_name}: <code>{cnt}</code>")

    if top_users:
        lines.append("\n🏆 <b>Топ-5 активных пользователей:</b>")
        for uname, g_key, reqs in top_users:
            tag = f"@{uname}" if uname and uname != "unknown" else "Без ника"
            g_str = (g_key or DEFAULT_GROUP).upper()
            lines.append(f" • {tag} ({g_str}): <code>{reqs}</code> запр.")

    if top_actions:
        lines.append("\n🔥 <b>Популярные действия за сутки:</b>")
        for act, cnt in top_actions:
            lines.append(f" • <code>{act}</code>: {cnt}")

    return "\n".join(lines)