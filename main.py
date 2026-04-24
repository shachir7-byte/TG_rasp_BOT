import asyncio
import re
import sqlite3
import aiohttp
import json
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest

# ⚙️ НАСТРОЙКИ
BOT_TOKEN = "8671137490:AAH4Gssdr1OGCojx0_VXzCCTVIw8xlglgg0"
PROXY_URL = None
ADMIN_ID = 720293880

# 🕐 ЧАСОВОЙ ПОЯС (Москва = UTC+3)
MSK_TIMEZONE = timezone(timedelta(hours=3))

# 📚 КОНФИГУРАЦИЯ ГРУПП
GROUPS = {
    "is": {
        "name": "ИС-23/9-3",
        "group_id": "f7fab01c-26fa-11ee-9f9a-c08a77b51a65",
        "week_id": "69e0cd5325ece715e4f993e2"
    },
    "d": {
        "name": "Д-23/9-3",
        "group_id": "c7b0c54e-38d4-11ee-9f9a-c08a77b51a65",
        "week_id": "69e0cd5325ece715e4f993e2"
    }
}

API_BASE = "https://sielom.ru/schedule/api/lessons/group"

# 🗓 ДНИ НЕДЕЛИ
DAYS_OF_WEEK = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
DAYS_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

# 🤖 ИНИЦИАЛИЗАЦИЯ
session_kwargs = {"proxy": PROXY_URL} if PROXY_URL else {}
session = AiohttpSession(**session_kwargs)
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

# 🗄 БАЗА ДАННЫХ
DB_FILE = "schedule.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            user_group TEXT DEFAULT 'is',
            notify_time TEXT,
            notify_type TEXT DEFAULT 'time',
            notify_offset INTEGER DEFAULT 0,
            request_count INTEGER DEFAULT 0,
            last_sent_date TEXT,
            first_seen TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            action TEXT,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_user(chat_id: int, username: str = None, group: str = 'is'):
    conn = sqlite3.connect(DB_FILE)
    now = datetime.now(MSK_TIMEZONE).isoformat()
    conn.execute("""
        INSERT OR IGNORE INTO users (chat_id, username, user_group, first_seen, request_count)
        VALUES (?, ?, ?, ?, 0)
    """, (chat_id, username, group, now))
    conn.execute("""
        UPDATE users SET username = COALESCE(?, username), user_group = COALESCE(?, user_group)
        WHERE chat_id = ?
    """, (username, group, chat_id))
    conn.commit()
    conn.close()

def get_user_group(chat_id: int) -> str:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute("SELECT user_group FROM users WHERE chat_id = ?", (chat_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 'is'

def log_action(chat_id: int, action: str):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        INSERT INTO stats (chat_id, action, timestamp)
        VALUES (?, ?, ?)
    """, (chat_id, action, datetime.now(MSK_TIMEZONE).isoformat()))
    conn.commit()
    conn.close()

def increment_request_count(chat_id: int):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        UPDATE users SET request_count = request_count + 1
        WHERE chat_id = ?
    """, (chat_id,))
    conn.commit()
    conn.close()

def save_notify(chat_id: int, time: str = None, notify_type: str = 'time', offset: int = 0):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        UPDATE users SET 
            notify_time = ?,
            notify_type = ?,
            notify_offset = ?
        WHERE chat_id = ?
    """, (time, notify_type, offset, chat_id))
    conn.commit()
    conn.close()

def cancel_notify(chat_id: int):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        UPDATE users SET 
            notify_time = NULL,
            notify_type = 'time',
            notify_offset = 0
        WHERE chat_id = ?
    """, (chat_id,))
    conn.commit()
    conn.close()

def get_user_notify(chat_id: int) -> tuple:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute("""
        SELECT notify_time, notify_type, notify_offset 
        FROM users WHERE chat_id = ?
    """, (chat_id,))
    row = cur.fetchone()
    conn.close()
    return row if row else (None, 'time', 0)

def get_active_users():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute("""
        SELECT chat_id, notify_time, notify_type, notify_offset, last_sent_date
        FROM users 
        WHERE notify_time IS NOT NULL 
           OR (notify_type = 'offset' AND notify_offset > 0)
    """)
    return cur.fetchall()

def update_last_sent(chat_id: int, date_str: str):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        UPDATE users SET last_sent_date = ? 
        WHERE chat_id = ?
    """, (date_str, chat_id))
    conn.commit()
    conn.close()

# 📊 СТАТИСТИКА
def get_stats_summary() -> str:
    conn = sqlite3.connect(DB_FILE)
    users = conn.execute("""
        SELECT chat_id, username, request_count, notify_time, notify_type, notify_offset, user_group
        FROM users ORDER BY request_count DESC
    """).fetchall()
    
    total_users = len(users)
    active_notifies = sum(1 for u in users if u[3] or (u[4] == 'offset' and u[5] > 0))
    total_requests = sum(u[2] for u in users)
    conn.close()
    
    lines = [
        "📊 **Статистика бота**\n",
        f"👥 Всего пользователей: **{total_users}**",
        f"🔔 Активных уведомлений: **{active_notifies}**",
        f"📈 Всего запросов: **{total_requests}**\n",
        "📋 **Пользователи:**\n"
    ]
    
    for chat_id, username, count, notify_time, notify_type, notify_offset, user_group in users:
        if username:
            name = f"[@{username}](https://t.me/{username})"
        else:
            name = f"[User {chat_id}](tg://user?id={chat_id})"
        
        group_name = GROUPS.get(user_group, {}).get('name', user_group)
        
        if notify_time:
            if notify_type == 'time':
                notify = f"⏰ {notify_time}"
            else:
                notify = f"⏳ За {notify_offset} ч до пары"
        elif notify_type == 'offset' and notify_offset > 0:
            notify = f"⏳ За {notify_offset} ч до пары"
        else:
            notify = "🔕"
        
        lines.append(f"• {name} — {group_name} — {count} запрос(ов) {notify}")
    
    return "\n".join(lines)

def clear_stats():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM stats")
    deleted = conn.total_changes
    conn.commit()
    conn.close()
    return deleted

# 📝 ФОРМАТИРОВАНИЕ
def format_schedule(title: str, lessons: list, is_week: bool = False) -> str:
    if not lessons:
        return f"📅 {title}\n🟢 Пар нет"
    
    if is_week:
        by_date = defaultdict(list)
        for lesson in lessons:
            by_date[lesson['date']].append(lesson)
        
        lines = [f"📅 {title}\n"]
        for date_str in sorted(by_date.keys()):
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                day_name_ru = DAYS_OF_WEEK[date_obj.weekday()]
                date_formatted = date_obj.strftime("%d.%m")
            except:
                day_name_ru, date_formatted = "", date_str
            
            lines.append(f"{'━'*5} {day_name_ru}, {date_formatted} {'━'*5}")
            for l in sorted(by_date[date_str], key=lambda x: x['lesson_number']):
                lines.append(f"{l['lesson_number']} пара | {l['time']}")
                lines.append(f"📚 {l['subject']}")
                lines.append(f"👨‍🏫 {l['teacher']} | 🚪 {l['room']}")
                lines.append("")
            lines.append("")
        return "\n".join(lines)
    else:
        lines = [f"📅 {title}\n"]
        for l in sorted(lessons, key=lambda x: x['lesson_number']):
            lines.append(f"{'━'*30}")
            lines.append(f"№{l['lesson_number']} пара | {l['time']}")
            lines.append(f"📚 {l['subject']}")
            lines.append(f"👨‍🏫 {l['teacher']}")
            lines.append(f"🚪 Ауд. {l['room']}")
        return "\n".join(lines)

# 🌐 API
async def fetch_schedule(group_key: str, target_date: str = None) -> list:
    group_config = GROUPS.get(group_key, GROUPS['is'])
    url = f"{API_BASE}/{group_config['group_id']}"
    params = {"week_id": group_config['week_id']}
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and 'error' in data:
                        return []
                    if isinstance(data, dict):
                        for key in ['data', 'lessons', 'items']:
                            if key in data and isinstance(data[key], list):
                                data = data[key]
                                break
                    if not isinstance(data, list):
                        data = [data] if isinstance(data, dict) else []
                    return parse_schedule_data(data, target_date)
    except Exception as e:
        print(f"API Error: {e}")
    return []

def parse_schedule_data(data: list, target_date: str = None) -> list:
    lessons = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if target_date and item.get('date') != target_date:
            continue
        
        subj = item.get('subject', {})
        teach = item.get('teacher', {})
        cab = item.get('cabinet', {})
        
        lessons.append({
            "time": f"{item.get('timeStart','')}-{item.get('timeEnd','')}" if item.get('timeStart') and item.get('timeEnd') else "?",
            "time_start": item.get('timeStart', ''),
            "subject": subj.get('name') or subj.get('abb_name', 'Нет предмета') if isinstance(subj, dict) else subj,
            "teacher": teach.get('name') or teach.get('abb_name', 'Нет преподавателя') if isinstance(teach, dict) else teach,
            "room": cab.get('number', '?') if isinstance(cab, dict) else cab,
            "lesson_number": item.get('lessonNumber', 99),
            "date": item.get('date', '')
        })
    return lessons

# 🔘 КЛАВИАТУРЫ
def get_group_select_keyboard():
    buttons = [
        [InlineKeyboardButton(text="📚 ИС-23/9-3", callback_data="group_is")],
        [InlineKeyboardButton(text="🎨 Д-23/9-3", callback_data="group_d")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_main_keyboard(chat_id: int = None):
    today = datetime.now(MSK_TIMEZONE)
    week_start = today - timedelta(days=today.weekday())
    
    buttons = []
    
    row1 = []
    for i in range(3):
        day_date = week_start + timedelta(days=i)
        is_today = " •" if i == today.weekday() else ""
        row1.append(InlineKeyboardButton(
            text=f"{DAYS_SHORT[i]}{is_today}", 
            callback_data=f"sch_day_{day_date.strftime('%Y-%m-%d')}"
        ))
    buttons.append(row1)
    
    row2 = []
    for i in range(3, 6):
        day_date = week_start + timedelta(days=i)
        is_today = " •" if i == today.weekday() else ""
        row2.append(InlineKeyboardButton(
            text=f"{DAYS_SHORT[i]}{is_today}", 
            callback_data=f"sch_day_{day_date.strftime('%Y-%m-%d')}"
        ))
    buttons.append(row2)
    
    buttons.append([
        InlineKeyboardButton(text="📆 Неделя", callback_data="sch_week"),
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
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]])

def get_notify_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏰ По времени", callback_data="notify_type_time")],
        [InlineKeyboardButton(text="⏳ За время до пары", callback_data="notify_type_offset")],
        [InlineKeyboardButton(text="🗑 Отключить", callback_data="notify_delete")],
        [InlineKeyboardButton(text="🏠 Назад", callback_data="main_menu")]
    ])

def get_notify_time_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕐 8:00", callback_data="notify_time_08:00")],
        [InlineKeyboardButton(text="🕐 9:00", callback_data="notify_time_09:00")],
        [InlineKeyboardButton(text="🕐 Свое время", callback_data="notify_time_custom")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="notify_menu")]
    ])

def get_notify_offset_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ За 1 час", callback_data="notify_offset_1")],
        [InlineKeyboardButton(text="⏳ За 2 часа", callback_data="notify_offset_2")],
        [InlineKeyboardButton(text="⏳ За 3 часа", callback_data="notify_offset_3")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="notify_menu")]
    ])

# 📱 ОБРАБОТЧИКИ
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    save_user(m.chat.id, m.from_user.username, 'is')
    increment_request_count(m.chat.id)
    log_action(m.chat.id, "start")
    await m.answer(
        "👋 **Привет! Я бот-расписание**\n\n"
        "Выберите вашу группу:",
        reply_markup=get_group_select_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(lambda c: c.data.startswith("group_"))
async def handle_group_select(c: types.CallbackQuery):
    group_key = c.data.replace("group_", "")
    group_name = GROUPS[group_key]['name']
    
    save_user(c.from_user.id, c.from_user.username, group_key)
    increment_request_count(c.from_user.id)
    log_action(c.from_user.id, f"group_select_{group_key}")
    
    try:
        await c.message.edit_text(
            f"✅ **Выбрана группа: {group_name}**\n\n"
            f"📅 Дни недели\n📆 Неделя\n⏰ Уведомления\n\n"
            f"Выбери день 👇",
            reply_markup=get_main_keyboard(c.from_user.id),
            parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await c.answer()

@dp.message(Command("menu"))
async def cmd_menu(m: types.Message):
    user_group = get_user_group(m.chat.id)
    group_name = GROUPS.get(user_group, {}).get('name', 'ИС-23/9-3')
    
    await m.answer(
        f"📚 **Группа: {group_name}**\n\n"
        f"📅 Дни недели\n📆 Неделя\n⏰ Уведомления\n\n"
        f"Выбери день 👇",
        reply_markup=get_main_keyboard(m.chat.id),
        parse_mode="Markdown"
    )

async def show_main_menu(message: types.Message):
    user_group = get_user_group(message.chat.id)
    group_name = GROUPS.get(user_group, {}).get('name', 'ИС-23/9-3')
    
    try:
        await message.edit_text(
            f"📚 **Группа: {group_name}**\n\n"
            f"📅 Дни недели\n📆 Неделя\n⏰ Уведомления\n\n"
            f"Выбери день 👇",
            reply_markup=get_main_keyboard(message.chat.id),
            parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

@dp.message(Command("stats"))
@dp.message(Command("clearstats"))
async def admin_commands(m: types.Message):
    if m.chat.id != ADMIN_ID:
        return await m.answer("🔐 Только админ", reply_markup=get_back_keyboard())
    if m.text == "/stats":
        await m.answer(get_stats_summary(), parse_mode="Markdown", reply_markup=get_back_keyboard())
    elif m.text == "/clearstats":
        deleted = clear_stats()
        await m.answer(f"🗑 Очищено: **{deleted}** записей", parse_mode="Markdown", reply_markup=get_back_keyboard())

@dp.message(Command("notify"))
async def cmd_set_notify(m: types.Message):
    args = m.text.split()
    if len(args) < 2 or not re.match(r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$", args[1]):
        return await m.answer("❌ Формат: `/notify 18:30`", parse_mode="Markdown", reply_markup=get_back_keyboard())
    save_notify(m.chat.id, args[1], 'time', 0)
    log_action(m.chat.id, f"notify_set_{args[1]}")
    await m.answer(f"✅ Уведомление: {args[1]}", reply_markup=get_back_keyboard())

@dp.message(Command("cancel_notify"))
async def cmd_cancel_notify(m: types.Message):
    cancel_notify(m.chat.id)
    log_action(m.chat.id, "notify_cancel")
    await m.answer("🔕 Уведомления отключены", reply_markup=get_back_keyboard())

@dp.message(lambda m: m.text and re.match(r'^\d{1,2}[:.]\d{2}$', m.text.strip()))
async def handle_time_input(m: types.Message):
    text = m.text.strip().replace('.', ':')
    match = re.match(r'^(\d{1,2}):(\d{2})$', text)
    
    if not match:
        return
    
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return
    
    time_str = f"{hour:02d}:{minute:02d}"
    save_notify(m.chat.id, time_str, 'time', 0)
    log_action(m.chat.id, f"notify_set_{time_str}")
    
    await m.answer(
        f"✅ **Настроено!**\n🕐 {time_str}\n📅 Буду присылать расписание",
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )

@dp.callback_query(lambda c: c.data == "noop")
async def handle_noop(c: types.CallbackQuery):
    await c.answer("Инфо-кнопка 😊", show_alert=True)

@dp.callback_query(lambda c: c.data == "main_menu")
async def handle_main_menu(c: types.CallbackQuery):
    await show_main_menu(c.message)
    await c.answer()

@dp.callback_query(lambda c: c.data == "change_group")
async def handle_change_group(c: types.CallbackQuery):
    try:
        await c.message.edit_text(
            "🔄 **Сменить группу**\n\n"
            "Выберите вашу группу:",
            reply_markup=get_group_select_keyboard(),
            parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await c.answer()

@dp.callback_query(lambda c: c.data == "admin_panel")
async def handle_admin_panel(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("🔐 Доступ запрещён", show_alert=True)
    
    try:
        await c.message.edit_text(
            "👑 **Админ-панель**\n\n"
            "**Команды:**\n"
            "/stats — Статистика\n"
            "/clearstats — Очистить\n"
            "/notify 18:30 — Тест\n\n"
            "Все действия логируются.",
            parse_mode="Markdown", 
            reply_markup=get_back_keyboard()
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await c.answer()

@dp.callback_query(lambda c: c.data.startswith("sch_") or c.data == "refresh_schedule")
async def handle_schedule(c: types.CallbackQuery):
    today = datetime.now(MSK_TIMEZONE)
    action = c.data
    user_group = get_user_group(c.from_user.id)
    
    if action.startswith("sch_day_"):
        target_date = action.replace("sch_day_", "")
        try:
            date_obj = datetime.strptime(target_date, "%Y-%m-%d")
            day_name = DAYS_OF_WEEK[date_obj.weekday()]
            title = f"{day_name}, {date_obj.strftime('%d.%m.%Y')}"
        except:
            title = target_date
        is_week = False
    elif action == "sch_week":
        target_date = None
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        title = f"Неделя ({week_start.strftime('%d.%m')} - {week_end.strftime('%d.%m')})"
        is_week = True
    elif action == "refresh_schedule":
        target_date = today.strftime("%Y-%m-%d")
        title = f"Сегодня, {today.strftime('%d.%m.%Y')} ({DAYS_OF_WEEK[today.weekday()]})"
        is_week = False
    else:
        await c.answer("❌ Неизвестная команда", show_alert=True)
        return
    
    increment_request_count(c.from_user.id)
    log_action(c.from_user.id, action)
    
    await c.answer("⏳ Загружаю...")
    lessons = await fetch_schedule(user_group, target_date)
    text = format_schedule(title, lessons, is_week=is_week)
    
    try:
        await c.message.edit_text(
            text, 
            parse_mode="Markdown", 
            reply_markup=get_schedule_keyboard()
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    
    await c.answer()

@dp.callback_query(lambda c: c.data == "notify_menu")
async def notify_menu(c: types.CallbackQuery):
    notify_time, notify_type, notify_offset = get_user_notify(c.from_user.id)
    log_action(c.from_user.id, "notify_menu")
    
    if notify_time:
        if notify_type == 'time':
            status = f"🕐 По времени: **{notify_time}**"
        else:
            status = f"⏳ За **{notify_offset} ч** до пары"
    elif notify_type == 'offset' and notify_offset > 0:
        status = f"⏳ За **{notify_offset} ч** до пары"
    else:
        status = "🔕 Отключено"
    
    try:
        await c.message.edit_text(
            f"⏰ **Уведомления**\n\n"
            f"{status}\n\n"
            f"Выберите тип уведомлений:",
            parse_mode="Markdown", 
            reply_markup=get_notify_main_menu()
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await c.answer()

@dp.callback_query(lambda c: c.data == "notify_type_time")
async def notify_type_time(c: types.CallbackQuery):
    try:
        await c.message.edit_text(
            "⏰ **По времени**\n\n"
            "Выберите время или введите свое:",
            parse_mode="Markdown", 
            reply_markup=get_notify_time_keyboard()
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await c.answer()

@dp.callback_query(lambda c: c.data == "notify_type_offset")
async def notify_type_offset(c: types.CallbackQuery):
    try:
        await c.message.edit_text(
            "⏳ **За время до пары**\n\n"
            "Расписание придет за выбранное время до первой пары:",
            parse_mode="Markdown", 
            reply_markup=get_notify_offset_keyboard()
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await c.answer()

@dp.callback_query(lambda c: c.data.startswith("notify_time_"))
async def notify_time_set(c: types.CallbackQuery):
    time_val = c.data.replace("notify_time_", "")
    if time_val == "custom":
        try:
            await c.message.edit_text(
                "✏️ **Свое время**\n"
                "Напиши время: `18:30`\n"
                "Или 🏠 назад",
                parse_mode="Markdown", 
                reply_markup=get_back_keyboard()
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
    else:
        save_notify(c.from_user.id, time_val, 'time', 0)
        log_action(c.from_user.id, f"notify_time_{time_val}")
        try:
            await c.message.edit_text(
                f"✅ **Настроено!**\n🕐 {time_val}",
                parse_mode="Markdown",
                reply_markup=get_notify_main_menu()
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
    await c.answer()

@dp.callback_query(lambda c: c.data.startswith("notify_offset_"))
async def notify_offset_set(c: types.CallbackQuery):
    offset = int(c.data.replace("notify_offset_", ""))
    save_notify(c.from_user.id, None, 'offset', offset)
    log_action(c.from_user.id, f"notify_offset_{offset}")
    try:
        await c.message.edit_text(
            f"✅ **Настроено!**\n⏳ За {offset} ч до пары",
            parse_mode="Markdown",
            reply_markup=get_notify_main_menu()
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await c.answer()

@dp.callback_query(lambda c: c.data == "notify_delete")
async def notify_delete(c: types.CallbackQuery):
    cancel_notify(c.from_user.id)
    log_action(c.from_user.id, "notify_delete")
    try:
        await c.message.edit_text(
            "🗑 **Уведомления отключены**",
            parse_mode="Markdown", 
            reply_markup=get_notify_main_menu()
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await c.answer()

@dp.callback_query(lambda c: c.data == "help")
async def handle_help(c: types.CallbackQuery):
    try:
        await c.message.edit_text(
            "ℹ️ **Помощь**\n\n"
            "**Команды:**\n"
            "/start — Меню\n"
            "/notify 18:30 — Уведомление\n"
            "/cancel_notify — Отключить\n"
            "/stats — Статистика (админ)\n\n"
            "**Кнопки:**\n"
            "📅 Пн-Сб — день\n"
            "📆 Неделя — всё\n"
            "⏰ Уведы — настройки\n"
            "🔄 Сменить группу",
            parse_mode="Markdown", 
            reply_markup=get_back_keyboard()
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await c.answer()

# ⏰ WORKER
async def get_first_lesson_time(group_key: str, target_date: str) -> str:
    lessons = await fetch_schedule(group_key, target_date)
    if lessons:
        first = min(lessons, key=lambda x: x['time_start'])
        return first['time_start']
    return None

def time_to_minutes(time_str: str) -> int:
    h, m = map(int, time_str.split(':'))
    return h * 60 + m

def minutes_to_time(minutes: int) -> str:
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"

async def notification_worker():
    print("✅ Worker запущен")
    while True:
        try:
            now = datetime.now(MSK_TIMEZONE)
            cur_time = now.strftime("%H:%M")
            today_str = now.strftime("%Y-%m-%d")
            
            if cur_time == "00:01":
                clear_stats()
                print("🧹 Логи очищены")
            
            for chat_id, notify_time, notify_type, notify_offset, last_sent in get_active_users():
                should_send = False
                user_group = get_user_group(chat_id)
                
                if notify_type == 'time' and cur_time == notify_time and last_sent != today_str:
                    should_send = True
                elif notify_type == 'offset' and notify_offset:
                    first_lesson = await get_first_lesson_time(user_group, today_str)
                    if first_lesson:
                        lesson_minutes = time_to_minutes(first_lesson)
                        notify_minutes = lesson_minutes - (notify_offset * 60)
                        if notify_minutes < 0:
                            notify_minutes += 24 * 60
                        notify_time_calc = minutes_to_time(notify_minutes)
                        if cur_time == notify_time_calc and last_sent != today_str:
                            should_send = True
                
                if should_send:
                    lessons = await fetch_schedule(user_group, today_str)
                    title = f"Сегодня, {now.strftime('%d.%m.%Y')} ({DAYS_OF_WEEK[now.weekday()]})"
                    text = format_schedule(title, lessons, is_week=False)
                    
                    try:
                        await bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=get_schedule_keyboard())
                        update_last_sent(chat_id, today_str)
                        log_action(chat_id, "notify_sent")
                        print(f"✅ Отправлено {chat_id}")
                    except Exception as e:
                        print(f"❌ Ошибка {chat_id}: {e}")
        except Exception as e:
            print(f"❌ Worker: {e}")
        await asyncio.sleep(30)

# 🚀 ЗАПУСК
async def main():
    init_db()
    print("✅ Бот запущен...")
    print(f"🕐 Часовой пояс: MSK (UTC+3)")
    print(f"📚 Группы:")
    for key, group in GROUPS.items():
        print(f"   {group['name']}: {group['group_id']}")
    print(f"👑 Админ ID: {ADMIN_ID}")
    asyncio.create_task(notification_worker())
    await dp.start_polling(bot)

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("🛑 Бот остановлен вручную")
            break
        except Exception as e:
            print(f"⚠️ Бот упал: {e}")
            print("🔄 Перезапуск через 5 секунд...")
            asyncio.run(asyncio.sleep(5))