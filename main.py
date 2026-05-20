import asyncio
import re
import logging
import platform
import os
import signal
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from logging.handlers import RotatingFileHandler

# 🔐 Загрузка переменных окружения
from dotenv import load_dotenv
load_dotenv()

# 🗄 Асинхронная БД
import aiosqlite

# 🌐 HTTP Сессия
import aiohttp

# 🤖 Aiogram
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramConflictError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# ⚙️ НАСТРОЙКИ ИЗ .ENV
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

if not BOT_TOKEN:
    raise ValueError("❌ Не найден BOT_TOKEN в файле .env!")

# 🕐 ЧАСОВОЙ ПОЯС
LOCAL_TIMEZONE = timezone(timedelta(hours=5))

# 📝 ЛОГИРОВАНИЕ
logger = logging.getLogger(__name__)

# Настройка handlers
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    
    # Файл (всё)
    file_handler = RotatingFileHandler("bot_log.txt", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(file_formatter)
    
    # Консоль (только ERROR и CRITICAL)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR)
    console_formatter = logging.Formatter("✅ %(levelname)s: %(message)s")
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

dp = Dispatcher()
DB_FILE = "schedule.db"
GLOBAL_SESSION = None

# --- СОСТОЯНИЯ (FSM) ---
class AdminStates(StatesGroup):
    broadcast_wait = State()

class UserStates(StatesGroup):
    wait_time_input = State()

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY, username TEXT, user_group TEXT DEFAULT 'is',
            notify_time TEXT, notify_type TEXT DEFAULT 'time', notify_offset INTEGER DEFAULT 0,
            request_count INTEGER DEFAULT 0, last_sent_date TEXT, first_seen TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, action TEXT, timestamp TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS groups_config (
            key TEXT PRIMARY KEY, name TEXT, group_id TEXT)""")
        
        default_groups = [
            ("is", "ИС-23/9-3", "f7fab01c-26fa-11ee-9f9a-c08a77b51a65"),
            ("d", "Д-23/9-1", "c7b0c54e-38d4-11ee-9f9a-c08a77b51a65")
        ]
        await db.executemany("INSERT OR IGNORE INTO groups_config (key, name, group_id) VALUES (?, ?, ?)", default_groups)
        await db.commit()
    logger.info("База данных готова")

# Хелперы БД
async def save_user(chat_id: int, username: str = None, group: str = 'is'):
    now = datetime.now(LOCAL_TIMEZONE).isoformat()
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO users (chat_id, username, user_group, first_seen, request_count) VALUES (?, ?, ?, ?, 0)", (chat_id, username, group, now))
        await db.execute("UPDATE users SET username = COALESCE(?, username), user_group = COALESCE(?, user_group) WHERE chat_id = ?", (username, group, chat_id))
        await db.commit()

async def get_user_group(chat_id: int) -> str:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT user_group FROM users WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 'is'

async def increment_request_count(chat_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET request_count = request_count + 1 WHERE chat_id = ?", (chat_id,))
        await db.commit()

async def log_action(chat_id: int, action: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO stats (chat_id, action, timestamp) VALUES (?, ?, ?)", (chat_id, action, datetime.now(LOCAL_TIMEZONE).isoformat()))
        await db.commit()

async def get_user_notify(chat_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT notify_time, notify_type, notify_offset FROM users WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row if row else (None, 'time', 0)

async def save_notify(chat_id: int, time: str = None, notify_type: str = 'time', offset: int = 0):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET notify_time = ?, notify_type = ?, notify_offset = ? WHERE chat_id = ?", (time, notify_type, offset, chat_id))
        await db.commit()

async def cancel_notify(chat_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET notify_time = NULL, notify_type = 'time', notify_offset = 0 WHERE chat_id = ?", (chat_id,))
        await db.commit()

async def get_active_users():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT chat_id, notify_time, notify_type, notify_offset, last_sent_date FROM users WHERE notify_time IS NOT NULL OR (notify_type = 'offset' AND notify_offset > 0)") as cursor:
            return await cursor.fetchall()

async def update_last_sent(chat_id: int, date_str: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET last_sent_date = ? WHERE chat_id = ?", (date_str, chat_id))
        await db.commit()

async def get_all_users_chat_ids():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT chat_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

async def get_user_count():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0]

async def get_groups_config():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT key, name, group_id FROM groups_config") as cursor:
            rows = await cursor.fetchall()
            return {r[0]: {"name": r[1], "group_id": r[2]} for r in rows}

async def get_full_admin_stats():
    async with aiosqlite.connect(DB_FILE) as db:
        groups = await get_groups_config()
        async with db.execute("""
            SELECT u.chat_id, u.username, u.user_group, u.request_count, u.first_seen, 
                   (SELECT s.action FROM stats s WHERE s.chat_id = u.chat_id ORDER BY s.timestamp DESC LIMIT 1) as last_act
            FROM users u ORDER BY u.request_count DESC
        """) as cursor:
            users = await cursor.fetchall()
    
    total_users = len(users)
    total_requests = sum(u[3] for u in users)
    
    lines = [f"📊 <b>Полная статистика</b>\n\n👥 Всего: <b>{total_users}</b>", f"📈 Запросов: <b>{total_requests}</b>\n━━━━━━━━━━━━━━━━━━━━━━\n📋 <b>Пользователи:</b>\n\n"]
    
    for chat_id, username, grp, count, first_seen, last_act in users:
        name = f"@{username}" if username else f"User {chat_id}"
        grp_name = groups.get(grp, {}).get('name', grp)
        act_info = f"<i>{last_act}</i>" if last_act else "—"
        lines.append(f"🆔 <code>{chat_id}</code> | <b>{name}</b> ({grp_name})")
        lines.append(f"   └ Запросов: {count} | Активность: {act_info}\n")
    
    return "\n".join(lines)

async def clear_stats():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM stats")
        deleted = db.total_changes
        await db.commit()
        return deleted

# 📝 ФОРМАТИРОВАНИЕ
def format_schedule(title: str, lessons: list, is_week: bool = False) -> str:
    DAYS_OF_WEEK = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
    if not lessons:
        return f"📅 <b>{title}</b>\n\n🟢 <i>Пар нет, отдыхай!</i>"
    
    if is_week:
        by_date = defaultdict(list)
        for lesson in lessons:
            by_date[lesson['date']].append(lesson)
        lines = [f"📅 <b>{title}</b>\n"]
        sorted_dates = sorted(by_date.keys())
        for idx, date_str in enumerate(sorted_dates):
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                day_name_ru = DAYS_OF_WEEK[date_obj.weekday()]
                date_formatted = date_obj.strftime("%d.%m")
            except:
                day_name_ru, date_formatted = "", date_str
            lines.append(f"\n<b>🗓 {day_name_ru} | {date_formatted}</b>")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            day_lessons = sorted(by_date[date_str], key=lambda x: x['lesson_number'])
            for l in day_lessons:
                lines.append(f"<b>{l['time']}</b>")
                lines.append(f"{l['subject']}")
                lines.append(f"<i>👤 {l['teacher']} | 🚪 {l['room']}</i>")
                lines.append("")
        return "\n".join(lines)
    else:
        lines = [f"📅 <b>{title}</b>\n", "━━━━━━━━━━━━━━━━━━━━━━"]
        for l in sorted(lessons, key=lambda x: x['lesson_number']):
            lines.append(f"\n<b>{l['time']}</b>")
            lines.append(f"{l['subject']}")
            lines.append(f"<i>👤 {l['teacher']}</i>")
            lines.append(f"🚪 Ауд. <b>{l['room']}</b>")
        return "\n".join(lines)

# 🌐 API
async def get_week_id_by_date(date_str: str) -> str:
    global GLOBAL_SESSION
    url = f"https://sielom.ru/schedule/api/weeks/date/{date_str}"
    try:
        async with GLOBAL_SESSION.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and len(data) > 0:
                    w_id = data[0].get('_id') or data[0].get('id')
                    if w_id: return w_id
                elif isinstance(data, dict):
                    w_id = data.get('_id') or data.get('id')
                    if w_id: return w_id
    except Exception as e:
        logger.error(f"Ошибка week_id: {e}")
    return None

async def fetch_schedule(group_key: str, target_date: str = None) -> list:
    groups = await get_groups_config()
    group_config = groups.get(group_key, groups.get('is'))
    group_id = group_config['group_id']
    req_date = target_date if target_date else datetime.now(LOCAL_TIMEZONE).strftime("%Y-%m-%d")
    if not target_date and datetime.now(LOCAL_TIMEZONE).weekday() == 6:
        next_monday = datetime.now(LOCAL_TIMEZONE) + timedelta(days=(7 - datetime.now(LOCAL_TIMEZONE).weekday()))
        req_date = next_monday.strftime("%Y-%m-%d")

    week_id = await get_week_id_by_date(req_date)
    if not week_id: return []

    url = f"https://sielom.ru/schedule/api/lessons/group/{group_id}"
    params = {"week_id": week_id}
    
    try:
        async with GLOBAL_SESSION.get(url, params=params, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                lessons_data = []
                if isinstance(data, list): lessons_data = data
                elif isinstance(data, dict): lessons_data = data.get('data', data.get('items', []))
                if target_date:
                    lessons_data = [l for l in lessons_data if isinstance(l, dict) and l.get('date') == target_date]
                return parse_schedule_data(lessons_data, target_date)
    except Exception as e:
        logger.error(f"Ошибка API: {e}")
    return []

def parse_schedule_data(data_list: list, target_date: str = None) -> list:
    lessons = []
    if not isinstance(data_list, list): return lessons
    for item in data_list:
        if not isinstance(item, dict): continue
        if target_date and item.get('date') != target_date: continue
        subj = item.get('subject', {})
        teach = item.get('teacher', {})
        cab = item.get('cabinet', {})
        s_name = subj.get('name') or subj.get('abb_name', 'Нет предмета') if isinstance(subj, dict) else str(subj) if subj else 'Нет предмета'
        t_name = teach.get('name') or teach.get('abb_name', 'Нет преп.') if isinstance(teach, dict) else str(teach) if teach else 'Нет преп.'
        r_num = cab.get('number', '?') if isinstance(cab, dict) else str(cab) if cab else '?'
        t_start = item.get('timeStart', '')
        t_end = item.get('timeEnd', '')
        time_str = f"{t_start}-{t_end}" if t_start and t_end else "??"
        lessons.append({
            "time": time_str, "time_start": t_start, "subject": s_name,
            "teacher": t_name, "room": r_num,
            "lesson_number": item.get('lessonNumber', 99), "date": item.get('date', '')
        })
    lessons.sort(key=lambda x: x['lesson_number'])
    return lessons

# 🔘 КЛАВИАТУРЫ
def get_group_select_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 ИС-23/9-3", callback_data="group_is")],
        [InlineKeyboardButton(text="🎨 Д-23/9-1", callback_data="group_d")]
    ])

def get_main_keyboard(chat_id: int = None):
    today = datetime.now(LOCAL_TIMEZONE)
    if today.weekday() == 6:
        week_start = today + timedelta(days=(7 - today.weekday()))
    else:
        week_start = today - timedelta(days=today.weekday())
    buttons = []
    row1, row2 = [], []
    DAYS_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    for i in range(6):
        day_date = week_start + timedelta(days=i)
        is_today = " •" if (today.weekday() != 6 and i == today.weekday()) else ""
        btn = InlineKeyboardButton(text=f"{DAYS_SHORT[i]}{is_today}", callback_data=f"sch_day_{day_date.strftime('%Y-%m-%d')}")
        if i < 3: row1.append(btn)
        else: row2.append(btn)
    buttons.extend([row1, row2])
    week_label = "Неделя (След.)" if today.weekday() == 6 else "Неделя"
    buttons.append([
        InlineKeyboardButton(text=f"📆 {week_label}", callback_data="sch_week"),
        InlineKeyboardButton(text="⏰ Уведы", callback_data="notify_menu")
    ])
    buttons.append([InlineKeyboardButton(text="🔄 Сменить группу", callback_data="change_group")])
    admin_row = [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")]
    if chat_id == ADMIN_ID:
        admin_row.append(InlineKeyboardButton(text="👑 Админ", callback_data="admin_panel"))
    buttons.append(admin_row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_schedule_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_schedule")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]])

def get_notify_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏰ По времени", callback_data="notify_type_time")],
        [InlineKeyboardButton(text="⏳ За время до", callback_data="notify_type_offset")],
        [InlineKeyboardButton(text="🗑 Отключить", callback_data="notify_delete")],
        [InlineKeyboardButton(text="🏠 Назад", callback_data="main_menu")]
    ])

def get_notify_time_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕐 8:00", callback_data="notify_time_08:00")],
        [InlineKeyboardButton(text="🕐 9:00", callback_data="notify_time_09:00")],
        [InlineKeyboardButton(text="✏️ Свое", callback_data="notify_time_custom")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="notify_menu")]
    ])

def get_notify_offset_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ За 1 час", callback_data="notify_offset_1")],
        [InlineKeyboardButton(text="⏳ За 2 часа", callback_data="notify_offset_2")],
        [InlineKeyboardButton(text="⏳ За 3 часа", callback_data="notify_offset_3")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="notify_menu")]
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🧪 Тест", callback_data="admin_test")],
        [InlineKeyboardButton(text="🗑 Очистить логи", callback_data="admin_clear")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])

# 🛡 ОБРАБОТЧИК ОШИБОК (ИСПРАВЛЕНО)
async def errors_handler(event: types.Update, exception: Exception):
    if isinstance(exception, TelegramConflictError):
        logger.warning("Конфликт версий бота! Проверьте, не запущен ли бот в другом месте.")
        return True # Игнорируем, чтобы не спамить
    if isinstance(exception, TelegramNetworkError):
        logger.warning(f"Проблемы с интернетом: {exception}")
        return True
    if isinstance(exception, TelegramBadRequest):
        # Ошибки редактирования сообщений (например, сообщение не изменено) игнорируем
        if "message is not modified" in str(exception):
            return True
        logger.warning(f"Bad Request: {exception}")
        return True
    
    logger.error(f"Критическая ошибка: {type(exception).__name__}: {exception}", exc_info=True)
    return True

dp.errors.register(errors_handler)

# 📱 ОБРАБОТЧИКИ
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await save_user(m.chat.id, m.from_user.username, 'is')
    await increment_request_count(m.chat.id)
    await log_action(m.chat.id, "start")
    await m.answer("👋 <b>Привет! Я бот-расписание</b>\n\nВыберите группу:", reply_markup=get_group_select_keyboard(), parse_mode="HTML")

@dp.callback_query(lambda c: c.data.startswith("group_"))
async def handle_group_select(c: types.CallbackQuery):
    group_key = c.data.replace("group_", "")
    groups = await get_groups_config()
    group_name = groups[group_key]['name']
    await save_user(c.from_user.id, c.from_user.username, group_key)
    await increment_request_count(c.from_user.id)
    await log_action(c.from_user.id, f"group_select_{group_key}")
    try:
        await c.message.edit_text(f"✅ <b>Группа: {group_name}</b>\n\nВыбери день 👇", reply_markup=get_main_keyboard(c.from_user.id), parse_mode="HTML")
    except TelegramBadRequest: pass
    await c.answer()

@dp.message(Command("menu"))
async def cmd_menu(m: types.Message):
    user_group = await get_user_group(m.chat.id)
    groups = await get_groups_config()
    group_name = groups.get(user_group, {}).get('name', 'ИС-23/9-3')
    await m.answer(f"📚 <b>Группа: {group_name}</b>\n\nВыбери день 👇", reply_markup=get_main_keyboard(m.chat.id), parse_mode="HTML")

async def show_main_menu(message: types.Message):
    user_group = await get_user_group(message.chat.id)
    groups = await get_groups_config()
    group_name = groups.get(user_group, {}).get('name', 'ИС-23/9-3')
    try:
        await message.edit_text(f"📚 <b>Группа: {group_name}</b>\n\nВыбери день 👇", reply_markup=get_main_keyboard(message.chat.id), parse_mode="HTML")
    except TelegramBadRequest: pass

@dp.callback_query(lambda c: c.data == "main_menu")
async def handle_main_menu(c: types.CallbackQuery):
    await show_main_menu(c.message)
    await c.answer()

@dp.callback_query(lambda c: c.data == "change_group")
async def handle_change_group(c: types.CallbackQuery):
    try:
        await c.message.edit_text("🔄 <b>Сменить группу</b>", reply_markup=get_group_select_keyboard(), parse_mode="HTML")
    except TelegramBadRequest: pass
    await c.answer()

# 🔐 АДМИН ПАНЕЛЬ
@dp.callback_query(lambda c: c.data == "admin_panel")
async def handle_admin_panel(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("🔐 Доступ запрещён!", show_alert=True)
    await log_action(c.from_user.id, "open_admin_panel")
    try:
        await c.message.edit_text("👑 <b>Админ-панель</b>\n\nВыберите действие:", parse_mode="HTML", reply_markup=get_admin_keyboard())
    except TelegramBadRequest: pass
    await c.answer()

@dp.callback_query(lambda c: c.data == "admin_stats")
async def handle_admin_stats(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return await c.answer("🔐", show_alert=True)
    await log_action(c.from_user.id, "view_stats")
    text = await get_full_admin_stats()
    try:
        await c.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_keyboard())
    except TelegramBadRequest:
        await c.message.answer(text, parse_mode="HTML", reply_markup=get_admin_keyboard())
    await c.answer()

@dp.callback_query(lambda c: c.data == "admin_test")
async def handle_admin_test(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return await c.answer("🔐", show_alert=True)
    await c.answer("🧪 Диагностика...", show_alert=False)
    await log_action(c.from_user.id, "run_test")
    report = ["🧪 <b>Отчет</b>\n"]
    try:
        count = await get_user_count()
        report.append(f"✅ <b>БД:</b> OK ({count} юзеров)")
    except Exception as e: report.append(f"❌ <b>БД:</b> {e}")
    try:
        lessons = await fetch_schedule('is', datetime.now(LOCAL_TIMEZONE).strftime("%Y-%m-%d"))
        report.append(f"✅ <b>API:</b> OK ({len(lessons)} пар)")
    except Exception as e: report.append(f"❌ <b>API:</b> {e}")
    report.append("\n✅ <b>Готов к работе.</b>")
    await c.message.answer("\n".join(report), parse_mode="HTML", reply_markup=get_admin_keyboard())

@dp.callback_query(lambda c: c.data == "admin_clear")
async def handle_admin_clear(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return await c.answer("🔐", show_alert=True)
    deleted = await clear_stats()
    await log_action(c.from_user.id, f"clear_stats_{deleted}")
    try:
        await c.message.edit_text(f"🗑 <b>Логи очищены!</b>\nУдалено: {deleted}", parse_mode="HTML", reply_markup=get_admin_keyboard())
    except TelegramBadRequest: pass
    await c.answer()

@dp.callback_query(lambda c: c.data == "admin_broadcast")
async def handle_admin_broadcast(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return await c.answer("🔐", show_alert=True)
    await state.set_state(AdminStates.broadcast_wait)
    await log_action(c.from_user.id, "start_broadcast")
    try:
        await c.message.edit_text(
            "📢 <b>Режим рассылки</b>\n\n"
            "1. <b>Всем:</b> просто напиши текст.\n"
            "2. <b>Лично:</b> напиши `ID: Текст` (например: `123456: Привет`).\n\n"
            "Напиши /cancel для выхода.",
            parse_mode="HTML"
        )
    except TelegramBadRequest: pass
    await c.answer()

@dp.callback_query(lambda c: c.data == "notify_time_custom")
async def notify_time_custom(c: types.CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.wait_time_input)
    try:
        await c.message.edit_text(
            "✏️ <b>Ввод времени</b>\n\n"
            "Напиши время в формате ЧЧ:ММ (например: 14:30)\n"
            "Или /cancel",
            parse_mode="HTML",
            reply_markup=get_back_keyboard()
        )
    except TelegramBadRequest: pass
    await c.answer()

@dp.message(Command("cancel"))
async def cmd_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Действие отменено.", reply_markup=get_back_keyboard())

# ЕДИНЫЙ ОБРАБОТЧИК ТЕКСТА
@dp.message(lambda m: m.text and not m.text.startswith('/'))
async def handle_text_input(m: types.Message, state: FSMContext):
    chat_id = m.chat.id
    text = m.text.strip()
    current_state = await state.get_state()
    
    # 1. Проверка состояния "Ввод времени"
    if current_state == UserStates.wait_time_input:
        if re.match(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$", text):
            await save_notify(chat_id, text, 'time', 0)
            await log_action(chat_id, f"notify_custom_{text}")
            await state.clear()
            await m.answer(f"✅ Установлено на <b>{text}</b>", parse_mode="HTML", reply_markup=get_notify_main_menu())
        else:
            await m.answer("❌ Неверный формат. Пример: 14:30\nПопробуй еще раз или /cancel")
        return

    # 2. Проверка состояния "Рассылка" (Только админ)
    if chat_id == ADMIN_ID and current_state == AdminStates.broadcast_wait:
        target_id = None
        message_text = text
        if ":" in text:
            parts = text.split(":", 1)
            if parts[0].strip().isdigit():
                target_id = int(parts[0].strip())
                message_text = parts[1].strip()
        
        if target_id:
            try:
                await m.bot.send_message(target_id, message_text, parse_mode="HTML")
                await m.answer(f"✅ Отправлено пользователю {target_id}")
                await log_action(chat_id, f"broadcast_to_{target_id}")
            except Exception as e:
                await m.answer(f"❌ Ошибка: {e}")
        else:
            users = await get_all_users_chat_ids()
            success, failed = 0, 0
            await m.answer(f"📢 Рассылка для {len(users)}...")
            for uid in users:
                try:
                    await m.bot.send_message(uid, message_text, parse_mode="HTML")
                    success += 1
                except: failed += 1
                await asyncio.sleep(0.05)
            await m.answer(f"✅ Успешно: {success}\n❌ Ошибок: {failed}")
            await log_action(chat_id, f"broadcast_all_{success}_{failed}")
        await state.clear()
        return

    # 3. Обычное сообщение
    await m.answer(
        "❓ <b>Команда не распознана.</b>\n\n"
        "Нажмите /start или выберите пункт в меню 👇",
        parse_mode="HTML",
        reply_markup=get_back_keyboard()
    )

@dp.message(lambda m: m.text and m.text.startswith('/') and m.text not in ['/start', '/menu', '/cancel'])
async def handle_unknown_command(m: types.Message):
    await m.answer("❓ <b>Такой команды нет.</b>\nНажми /start", parse_mode="HTML", reply_markup=get_back_keyboard())

@dp.callback_query(lambda c: c.data.startswith("sch_") or c.data == "refresh_schedule")
async def handle_schedule(c: types.CallbackQuery):
    today = datetime.now(LOCAL_TIMEZONE)
    action = c.data
    user_group = await get_user_group(c.from_user.id)
    target_date = None
    title = ""
    is_week = False
    
    if action.startswith("sch_day_"):
        target_date = action.replace("sch_day_", "")
        try:
            d_obj = datetime.strptime(target_date, "%Y-%m-%d")
            DAYS_OF_WEEK = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            title = f"{DAYS_OF_WEEK[d_obj.weekday()]}, {d_obj.strftime('%d.%m.%Y')}"
            if today.weekday() == 6 and d_obj.weekday() == 0: title += " (След. неделя)"
        except: title = target_date
    elif action == "sch_week":
        is_week = True
        DAYS_OF_WEEK = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
        if today.weekday() == 6:
            start = today + timedelta(days=(7 - today.weekday()))
            title = f"Неделя ({start.strftime('%d.%m')} - {(start+timedelta(days=6)).strftime('%d.%m')}) (След.)"
        else:
            start = today - timedelta(days=today.weekday())
            title = f"Неделя ({start.strftime('%d.%m')} - {(start+timedelta(days=6)).strftime('%d.%m')})"
    elif action == "refresh_schedule":
        if today.weekday() == 6:
            next_mon = today + timedelta(days=(7 - today.weekday()))
            target_date = next_mon.strftime("%Y-%m-%d")
            DAYS_OF_WEEK = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            title = f"Понедельник, {next_mon.strftime('%d.%m.%Y')} (След. неделя)"
        else:
            target_date = today.strftime("%Y-%m-%d")
            DAYS_OF_WEEK = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            title = f"Сегодня, {today.strftime('%d.%m.%Y')}"

    await increment_request_count(c.from_user.id)
    await log_action(c.from_user.id, action)
    await c.answer("⏳ Загрузка...")
    lessons = await fetch_schedule(user_group, target_date if not is_week else None)
    text = format_schedule(title, lessons, is_week=is_week)
    try:
        await c.message.edit_text(text, parse_mode="HTML", reply_markup=get_schedule_keyboard())
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.error(f"Ошибка редактирования: {e}")
    await c.answer()

@dp.callback_query(lambda c: c.data == "notify_menu")
async def notify_menu(c: types.CallbackQuery):
    n_time, n_type, n_off = await get_user_notify(c.from_user.id)
    status = "🔕 Отключено"
    if n_time: status = f"🕐 {n_time}"
    elif n_type == 'offset' and n_off > 0: status = f"⏳ За {n_off}ч"
    try:
        await c.message.edit_text(f"⏰ <b>Уведомления</b>\nСтатус: {status}", parse_mode="HTML", reply_markup=get_notify_main_menu())
    except TelegramBadRequest: pass
    await c.answer()

@dp.callback_query(lambda c: c.data == "notify_type_time")
async def notify_type_time(c: types.CallbackQuery):
    try:
        await c.message.edit_text("⏰ <b>По времени</b>", parse_mode="HTML", reply_markup=get_notify_time_keyboard())
    except TelegramBadRequest: pass
    await c.answer()

@dp.callback_query(lambda c: c.data == "notify_type_offset")
async def notify_type_offset(c: types.CallbackQuery):
    try:
        await c.message.edit_text("⏳ <b>За время до пары</b>", parse_mode="HTML", reply_markup=get_notify_offset_keyboard())
    except TelegramBadRequest: pass
    await c.answer()

@dp.callback_query(lambda c: c.data.startswith("notify_time_"))
async def notify_set_time(c: types.CallbackQuery):
    val = c.data.replace("notify_time_", "")
    if val == "custom": return
    await save_notify(c.from_user.id, val, 'time', 0)
    await log_action(c.from_user.id, f"notify_{val}")
    try:
        await c.message.edit_text(f"✅ Установлено на {val}", reply_markup=get_notify_main_menu())
    except TelegramBadRequest: pass
    await c.answer()

@dp.callback_query(lambda c: c.data.startswith("notify_offset_"))
async def notify_set_offset(c: types.CallbackQuery):
    off = int(c.data.replace("notify_offset_", ""))
    await save_notify(c.from_user.id, None, 'offset', off)
    await log_action(c.from_user.id, f"notify_off_{off}")
    try:
        await c.message.edit_text(f"✅ Установлено: за {off}ч", reply_markup=get_notify_main_menu())
    except TelegramBadRequest: pass
    await c.answer()

@dp.callback_query(lambda c: c.data == "notify_delete")
async def notify_del(c: types.CallbackQuery):
    await cancel_notify(c.from_user.id)
    try:
        await c.message.edit_text("🔕 Уведомления отключены", reply_markup=get_notify_main_menu())
    except TelegramBadRequest: pass
    await c.answer()

@dp.callback_query(lambda c: c.data == "help")
async def help_cmd(c: types.CallbackQuery):
    try:
        await c.message.edit_text("ℹ️ <b>Помощь</b>\n/start - Меню\n/notify - Настройка", parse_mode="HTML", reply_markup=get_back_keyboard())
    except TelegramBadRequest: pass
    await c.answer()

# ⏰ WORKER
async def notification_worker(bot: Bot):
    logger.info("Worker запущен")
    while True:
        try:
            now = datetime.now(LOCAL_TIMEZONE)
            cur_time = now.strftime("%H:%M")
            today_str = now.strftime("%Y-%m-%d")
            if cur_time == "00:01": 
                await clear_stats()
                logger.info("Логи статистики очищены")
            
            active_users = await get_active_users()
            for chat_id, n_time, n_type, n_off, last_sent in active_users:
                if last_sent == today_str: continue
                should_send = False
                if n_type == 'time' and cur_time == n_time: should_send = True
                elif n_type == 'offset' and n_off > 0:
                    lessons = await fetch_schedule(await get_user_group(chat_id), today_str)
                    if lessons:
                        valid = [l for l in lessons if l['time_start']]
                        if valid:
                            first = min(valid, key=lambda x: x['time_start'])
                            h, m = map(int, first['time_start'].split(':'))
                            lesson_min = h * 60 + m
                            notify_min = lesson_min - (n_off * 60)
                            if notify_min >= 0:
                                nh, nm = divmod(notify_min, 60)
                                if cur_time == f"{nh:02d}:{nm:02d}": should_send = True
                
                if should_send:
                    lessons = await fetch_schedule(await get_user_group(chat_id), today_str)
                    title = f"🔔 Расписание на {now.strftime('%d.%m')}"
                    text = format_schedule(title, lessons, is_week=False)
                    try:
                        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=get_schedule_keyboard())
                        await update_last_sent(chat_id, today_str)
                        logger.info(f"Уведомление отправлено {chat_id}")
                    except Exception as e:
                        logger.error(f"Ошибка уведомления: {e}")
        except Exception as e:
            logger.error(f"Ошибка воркера: {e}")
        await asyncio.sleep(30)

# 🚀 ЗАПУСК
async def run_bot():
    global GLOBAL_SESSION
    await init_db()
    
    connector = aiohttp.TCPConnector(limit=10)
    GLOBAL_SESSION = aiohttp.ClientSession(connector=connector)
    session = AiohttpSession()
    bot = Bot(token=BOT_TOKEN, session=session)
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook сброшен")
    except Exception as e:
        logger.warning(f"Не удалось сбросить webhook: {e}")

    # Уведомление о запуске
    await bot.send_message(ADMIN_ID, f"🤖 <b>БОТ ЗАПУЩЕН</b>\n🕒 {datetime.now(LOCAL_TIMEZONE).strftime('%H:%M')}\n👥 Пользователей: {await get_user_count()}", parse_mode="HTML")
    all_users = await get_all_users_chat_ids()
    success_count = 0
    print("📢 Уведомление о перезапуске...")
    for uid in all_users:
        if uid == ADMIN_ID: continue
        try:
            await bot.send_message(uid, "✅ <b>Бот обновлен!</b>\nРаботает быстрее и стабильнее.", parse_mode="HTML")
            success_count += 1
        except: pass
        await asyncio.sleep(0.05)
    if success_count > 0:
        print(f"✅ Уведомлено: {success_count}")

    print("✅ Бот запущен (Stable Version)!")
    worker_task = asyncio.create_task(notification_worker(bot))
    
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), skip_updates=True)
    except KeyboardInterrupt:
        print("--- БОТ ОСТАНОВЛЕН ---")
        logger.info("Остановка по Ctrl+C")
    except TelegramConflictError:
        print("❌ КОНФЛИКТ: Бот уже запущен в другом месте!")
        logger.error("Запуск прерван из-за конфликта версий (другой экземпляр бота работает).")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        worker_task.cancel()
        await GLOBAL_SESSION.close()
        await bot.session.close()
        await bot.close()
        print("✅ Соединения закрыты.")

if __name__ == "__main__":
    try:
        logger.info("--- ЗАПУСК БОТА (STABLE VERSION) ---")
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass