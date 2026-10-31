import asyncio
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from aiogram import Router, F, Bot, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery

from config import LOCAL_TIMEZONE_OFFSET, ADMIN_ID
from database import (
    get_user_group, increment_request_count, log_action,
    get_groups_config, save_user, update_notify_settings,
    cancel_notify, get_user_settings, get_all_users_chat_ids
)
from utils.api_client import get_schedule
from utils.formatter import format_schedule_text

logger = logging.getLogger(__name__)
router = Router()
LOCAL_TZ = timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET))

# --- СОСТОЯНИЯ FSM ---

class UserStates(StatesGroup):
    wait_custom_time = State()
    wait_avatar_photo = State()
    wait_broadcast_text = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДАТ ---

def get_week_bounds(now_dt: datetime, shift_weeks: int = 0):
    """
    Вычисляет даты понедельника и субботы с учетом сдвига недель (shift_weeks).
    В воскресенье базовой неделей считается наступающая рабочая неделя.
    """
    if now_dt.weekday() == 6:
        base_monday = now_dt + timedelta(days=1)
    else:
        base_monday = now_dt - timedelta(days=now_dt.weekday())

    target_monday = base_monday + timedelta(weeks=shift_weeks)
    target_saturday = target_monday + timedelta(days=5)
    return target_monday, target_saturday

# --- КЛАВИАТУРЫ ---

def get_group_select_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 ИС-23/9-3", callback_data="group_is")],
        [InlineKeyboardButton(text="🎨 Д-23/9-1", callback_data="group_d")]
    ])

def get_main_keyboard(chat_id: int = None):
    today = datetime.now(LOCAL_TZ)
    if today.weekday() == 6:
        week_start = today + timedelta(days=1)
    else:
        week_start = today - timedelta(days=today.weekday())
    
    buttons = []
    row1, row2 = [], []
    DAYS_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб']
    
    for i in range(6):
        day_date = week_start + timedelta(days=i)
        if today.weekday() != 6 and i == today.weekday():
            btn_text = f"• {DAYS_SHORT[i]} •"
        else:
            btn_text = DAYS_SHORT[i]
        
        btn = InlineKeyboardButton(text=btn_text, callback_data=f"sch_day_{day_date.strftime('%Y-%m-%d')}")
        if i < 3: 
            row1.append(btn)
        else: 
            row2.append(btn)
    
    buttons.append(row1)
    buttons.append(row2)
    
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

def get_schedule_nav_keyboard(current_action: str):
    """
    Клавиатура навигации под блоком расписания:
    - Переключение между текущей и следующей неделей с точным интервалом дат.
    - Кнопка принудительного обновления из API.
    - Кнопка возврата в главное меню.
    """
    today = datetime.now(LOCAL_TZ)
    curr_mon, curr_sat = get_week_bounds(today, shift_weeks=0)
    next_mon, next_sat = get_week_bounds(today, shift_weeks=1)

    curr_str = f"{curr_mon.strftime('%d.%m')} - {curr_sat.strftime('%d.%m')}"
    next_str = f"{next_mon.strftime('%d.%m')} - {next_sat.strftime('%d.%m')}"

    keyboard = []

    if current_action == "sch_week":
        keyboard.append([
            InlineKeyboardButton(text=f"📆 След. неделя ({next_str}) ❯", callback_data="sch_next_week")
        ])
    elif current_action == "sch_next_week":
        keyboard.append([
            InlineKeyboardButton(text=f"❮ 📆 Тек. неделя ({curr_str})", callback_data="sch_week")
        ])

    keyboard.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=current_action)])
    keyboard.append([InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_notify_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏰ По времени", callback_data="notify_type_time")],
        [InlineKeyboardButton(text="⏳ За время до", callback_data="notify_type_offset")],
        [InlineKeyboardButton(text="🗑 Отключить", callback_data="notify_delete")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
    ])

def get_notify_time_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕐 08:00", callback_data="notify_time_08:00")],
        [InlineKeyboardButton(text="🕐 09:00", callback_data="notify_time_09:00")],
        [InlineKeyboardButton(text="✏️ Свое время", callback_data="notify_time_custom")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="notify_menu")]
    ])

def get_notify_offset_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ За 1 час", callback_data="notify_offset_1")],
        [InlineKeyboardButton(text="⏳ За 2 часа", callback_data="notify_offset_2")],
        [InlineKeyboardButton(text="⏳ За 3 часа", callback_data="notify_offset_3")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="notify_menu")]
    ])

def get_help_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Донат и Магазин", callback_data="donate_menu")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
    ])

def get_donate_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Сменить аву бота — 67 ⭐", callback_data="buy_avatar_67")],
        [InlineKeyboardButton(text="📢 Общее сообщение всем — 20 ⭐", callback_data="buy_broadcast_20")],
        [InlineKeyboardButton(text="☕ Поддержать автора — 15 ⭐", callback_data="buy_support_15")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="help")]
    ])

def get_cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")]
    ])

# --- ОБРАБОТЧИКИ КОМАНД И МЕНЮ ---

@router.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    await save_user(m.chat.id, m.from_user.username, 'is')
    await log_action(m.chat.id, "start")
    kb = get_main_keyboard(m.chat.id)
    await m.answer("👋 <b>Привет! Я бот-расписание</b>\n\nВыберите группу или день:", reply_markup=kb, parse_mode="HTML")

@router.message(Command("menu"))
async def cmd_menu(m: types.Message, state: FSMContext):
    await state.clear()
    group_key = await get_user_group(m.chat.id)
    groups = await get_groups_config()
    group_name = groups.get(group_key, {}).get('name', 'ИС')
    kb = get_main_keyboard(m.chat.id)
    await m.answer(f"📚 <b>Группа: {group_name}</b>\n\nВыбери день 👇", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("group_"))
async def handle_group_select(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    group_key = c.data.replace("group_", "")
    groups = await get_groups_config()
    group_name = groups.get(group_key, {}).get('name', 'Неизвестно')
    
    await save_user(c.from_user.id, c.from_user.username, group_key)
    await log_action(c.from_user.id, f"group_select_{group_key}")
    
    try:
        await c.message.edit_text(
            f"✅ <b>Группа: {group_name}</b>\n\nВыбери день 👇",
            reply_markup=get_main_keyboard(c.from_user.id),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "main_menu")
async def handle_main_menu(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    group_key = await get_user_group(c.from_user.id)
    groups = await get_groups_config()
    group_name = groups.get(group_key, {}).get('name', 'ИС')
    try:
        await c.message.edit_text(
            f"📚 <b>Группа: {group_name}</b>\n\nВыбери день 👇",
            reply_markup=get_main_keyboard(c.from_user.id),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "change_group")
async def handle_change_group(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.edit_text("🔄 <b>Сменить группу</b>", reply_markup=get_group_select_keyboard(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

# --- УВЕДОМЛЕНИЯ И СВОЁ ВРЕМЯ ---

@router.callback_query(F.data == "notify_menu")
async def handle_notify_menu(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    settings = await get_user_settings(c.from_user.id)
    status = "🔕 Отключено"
    if settings:
        n_time, n_type, n_off = settings[3], settings[4], settings[5]
        if n_type == 'time' and n_time:
            status = f"🕐 Ежедневно в {n_time}"
        elif n_type == 'offset' and n_off > 0:
            status = f"⏳ За {n_off} ч. до пары"
            
    text = f"⏰ <b>Настройки уведомлений</b>\n\nСтатус: {status}\n\nВыберите тип:"
    try:
        await c.message.edit_text(text, reply_markup=get_notify_menu_kb(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "notify_type_time")
async def handle_notify_type_time(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.edit_text("⏰ <b>Уведомлять в определенное время</b>", reply_markup=get_notify_time_kb(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "notify_type_offset")
async def handle_notify_type_offset(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.edit_text("⏳ <b>Уведомлять за время до первой пары</b>", reply_markup=get_notify_offset_kb(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data.startswith("notify_time_"))
async def handle_notify_set_time(c: types.CallbackQuery, state: FSMContext):
    val = c.data.replace("notify_time_", "")
    if val == "custom":
        await state.set_state(UserStates.wait_custom_time)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="notify_type_time")]
        ])
        try:
            await c.message.edit_text(
                "✏️ <b>Введите своё время</b>\n\n"
                "Напишите время в формате <code>ЧЧ:ММ</code> (например, <code>07:30</code> или <code>06:45</code>):",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception:
            pass
        await c.answer()
        return
        
    await update_notify_settings(c.from_user.id, time=val, n_type='time', offset=0)
    await log_action(c.from_user.id, f"notify_set_{val}")
    try:
        await c.message.edit_text(f"✅ Уведомления установлены на <b>{val}</b>", reply_markup=get_notify_menu_kb(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.message(UserStates.wait_custom_time)
async def process_custom_time(m: types.Message, state: FSMContext):
    if not m.text:
        return
    raw_text = m.text.strip()
    if raw_text.lower() in ['/cancel', 'отмена', '/start']:
        await state.clear()
        await m.answer("❌ Установка времени отменена.", reply_markup=get_notify_menu_kb())
        return

    match = re.match(r"^(\d{1,2}):(\d{2})$", raw_text)
    if not match:
        await m.answer(
            "⚠️ <b>Неверный формат!</b>\nВведите время через двоеточие, например: <code>07:30</code>",
            parse_mode="HTML"
        )
        return

    hours, minutes = int(match.group(1)), int(match.group(2))
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        await m.answer(
            "⚠️ <b>Некорректное время!</b>\nЧасы должны быть от 00 до 23, а минуты — от 00 до 59.",
            parse_mode="HTML"
        )
        return

    formatted_time = f"{hours:02d}:{minutes:02d}"
    await update_notify_settings(m.from_user.id, time=formatted_time, n_type='time', offset=0)
    await log_action(m.from_user.id, f"notify_set_custom_{formatted_time}")
    await state.clear()

    await m.answer(
        f"✅ Своё время уведомлений установлено на <b>{formatted_time}</b>!",
        reply_markup=get_notify_menu_kb(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("notify_offset_"))
async def handle_notify_set_offset(c: types.CallbackQuery):
    off = int(c.data.replace("notify_offset_", ""))
    await update_notify_settings(c.from_user.id, time=None, n_type='offset', offset=off)
    await log_action(c.from_user.id, f"notify_offset_{off}")
    try:
        await c.message.edit_text(f"✅ Установлено: за <b>{off} ч.</b> до пары", reply_markup=get_notify_menu_kb(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "notify_delete")
async def handle_notify_delete(c: types.CallbackQuery):
    await cancel_notify(c.from_user.id)
    await log_action(c.from_user.id, "notify_disabled")
    try:
        await c.message.edit_text("🔕 <b>Уведомления отключены</b>", reply_markup=get_notify_menu_kb(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

# --- ПОМОЩЬ, ДОНАТЫ И МАГАЗИН ЗА ЗВЁЗДЫ (TELEGRAM STARS) ---

@router.callback_query(F.data == "help")
async def handle_help(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "Этот бот показывает расписание занятий и умеет присылать уведомления перед парами.\n"
        "Используй кнопки внизу для навигации.\n\n"
        "/start — Главное меню\n"
        "/menu — Показать расписание\n\n"
        "⭐ <i>Ты также можешь поддержать бота звёздами или заказать кастомные фичи в магазине ниже!</i>"
    )
    try:
        await c.message.edit_text(text, reply_markup=get_help_kb(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "donate_menu")
async def handle_donate_menu(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text = (
        "⭐ <b>Магазин и поддержка (Telegram Stars)</b>\n\n"
        "Выбери действие:\n"
        "• 🖼 <b>Сменить аву бота (67 ⭐)</b> — после оплаты ты сможешь отправить любую адекватную картинку, и мы поставим её на аватарку бота.\n"
        "• 📢 <b>Общее сообщение (20 ⭐)</b> — твой текст увидят все пользователи бота с подписью, от кого это сообщение.\n"
        "• ☕ <b>Поддержать автора (15 ⭐)</b> — просто закинуть звёзд на развитие и хостинг."
    )
    try:
        await c.message.edit_text(text, reply_markup=get_donate_menu_kb(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data.in_({"buy_avatar_67", "buy_broadcast_20", "buy_support_15"}))
async def handle_buy_stars(c: types.CallbackQuery):
    item = c.data
    if item == "buy_avatar_67":
        title = "🖼 Смена аватарки бота"
        description = "Право установить свою картинку на аватарку бота"
        amount = 67
        payload = "stars_avatar_67"
    elif item == "buy_broadcast_20":
        title = "📢 Общее сообщение всем"
        description = "Отправка вашего сообщения всем пользователям бота"
        amount = 20
        payload = "stars_broadcast_20"
    else:
        title = "☕ Поддержка проекта"
        description = "Донат на развитие и сервер для бота"
        amount = 15
        payload = "stars_support_15"

    await c.bot.send_invoice(
        chat_id=c.from_user.id,
        title=title,
        description=description,
        payload=payload,
        provider_token="",  # Строго пустая строка для Telegram Stars (XTR)
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=amount)]
    )
    await c.answer()

@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def process_successful_payment(m: types.Message, state: FSMContext):
    payload = m.successful_payment.invoice_payload
    await log_action(m.from_user.id, f"paid_{payload}")

    if payload == "stars_avatar_67":
        await state.set_state(UserStates.wait_avatar_photo)
        await m.answer(
            "🎉 <b>Оплата 67 ⭐ прошла успешно!</b>\n\n"
            "📸 Теперь отправь мне <b>фотографию</b> (именно как картинку, не файлом), которую нужно поставить на аватарку бота.\n"
            "<i>Для отмены напиши /cancel</i>",
            reply_markup=get_cancel_kb(),
            parse_mode="HTML"
        )
    elif payload == "stars_broadcast_20":
        await state.set_state(UserStates.wait_broadcast_text)
        await m.answer(
            "🎉 <b>Оплата 20 ⭐ прошла успешно!</b>\n\n"
            "✍️ Напиши одним сообщением <b>текст</b>, который мы разошлём всем пользователям бота от твоего имени.\n"
            "<i>Для отмены напиши /cancel</i>",
            reply_markup=get_cancel_kb(),
            parse_mode="HTML"
        )
    elif payload == "stars_support_15":
        await m.answer(
            "💖 <b>Огромное спасибо за поддержку!</b>\nТвои звёзды помогут боту работать ещё быстрее и стабильнее!",
            reply_markup=get_main_keyboard(m.chat.id),
            parse_mode="HTML"
        )
        if ADMIN_ID:
            user_tag = f"@{m.from_user.username}" if m.from_user.username else f"ID {m.from_user.id}"
            try:
                await m.bot.send_message(ADMIN_ID, f"☕ Пользователь {user_tag} задонатил <b>15 ⭐</b>!", parse_mode="HTML")
            except Exception:
                pass

@router.message(UserStates.wait_avatar_photo)
async def process_avatar_photo(m: types.Message, state: FSMContext):
    if m.text and m.text.strip().lower() in ['/cancel', 'отмена', '/start']:
        await state.clear()
        await m.answer("❌ Действие отменено.", reply_markup=get_main_keyboard(m.chat.id))
        return

    if not m.photo:
        await m.answer(
            "⚠️ Пожалуйста, отправь именно <b>картинку/фотографию</b> (или напиши /cancel для отмены)!",
            reply_markup=get_cancel_kb(),
            parse_mode="HTML"
        )
        return

    photo_file_id = m.photo[-1].file_id
    user_tag = f"@{m.from_user.username}" if m.from_user.username else html.escape(m.from_user.full_name)

    if ADMIN_ID:
        try:
            await m.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photo_file_id,
                caption=(
                    f"🖼 <b>Оплачена смена аватарки (67 ⭐)!</b>\n"
                    f"От кого: {user_tag} (<code>{m.from_user.id}</code>)\n\n"
                    f"Поставь это фото через @BotFather -> Edit Botpic."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Не удалось переслать фото админу: {e}")

    await state.clear()
    await log_action(m.from_user.id, "avatar_photo_submitted")
    await m.answer(
        "✅ <b>Картинка принята и передана на установку!</b>\nСкоро аватарка бота обновится.",
        reply_markup=get_main_keyboard(m.chat.id),
        parse_mode="HTML"
    )

async def _run_paid_broadcast(bot: Bot, sender_id: int, users: list, broadcast_message: str):
    sent = 0
    for uid in users:
        try:
            await bot.send_message(uid, broadcast_message, parse_mode="HTML")
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await log_action(sender_id, f"paid_broadcast_finished_{sent}")

@router.message(UserStates.wait_broadcast_text)
async def process_paid_broadcast_text(m: types.Message, state: FSMContext):
    if m.text and m.text.strip().lower() in ['/cancel', 'отмена', '/start']:
        await state.clear()
        await m.answer("❌ Рассылка отменена.", reply_markup=get_main_keyboard(m.chat.id))
        return

    if not m.text:
        await m.answer("⚠️ Пожалуйста, напиши текстовое сообщение для рассылки (или /cancel для отмены).", reply_markup=get_cancel_kb())
        return

    if len(m.text.strip()) > 1000:
        await m.answer("⚠️ Сообщение слишком длинное (максимум 1000 символов). Сократи его и отправь снова:")
        return

    await state.clear()
    sender_name = f"@{m.from_user.username}" if m.from_user.username else html.escape(m.from_user.full_name)
    formatted_user_text = m.html_text.strip()
    
    broadcast_message = (
        f"💬 <b>Общее сообщение от {sender_name}:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{formatted_user_text}"
    )

    users = await get_all_users_chat_ids()
    asyncio.create_task(_run_paid_broadcast(m.bot, m.from_user.id, users, broadcast_message))

    await m.answer(
        "🚀 <b>Твоё сообщение отправлено в очередь рассылки и рассылается прямо сейчас!</b>",
        reply_markup=get_main_keyboard(m.chat.id),
        parse_mode="HTML"
    )

# --- РАСПИСАНИЕ ---

@router.callback_query(F.data.startswith("sch_"))
async def handle_schedule(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    action = c.data
    user_group = await get_user_group(c.from_user.id)
    await increment_request_count(c.from_user.id)
    await log_action(c.from_user.id, action)
    
    target_date = None
    title = ""
    is_week = False
    today = datetime.now(LOCAL_TZ)
    
    # 1. Текущая неделя (в воскресенье берется наступающая)
    if action == "sch_week":
        is_week = True
        start_mon, end_sat = get_week_bounds(today, shift_weeks=0)
        suffix = " (След.)" if today.weekday() == 6 else ""
        title = f"Неделя ({start_mon.strftime('%d.%m')} - {end_sat.strftime('%d.%m')}){suffix}"
        target_date = start_mon.strftime("%Y-%m-%d")

    # 2. Следующая неделя со сдвигом на +1 неделю вперед
    elif action == "sch_next_week":
        is_week = True
        start_mon, end_sat = get_week_bounds(today, shift_weeks=1)
        title = f"След. неделя ({start_mon.strftime('%d.%m')} - {end_sat.strftime('%d.%m')})"
        target_date = start_mon.strftime("%Y-%m-%d")

    # 3. Конкретный день недели
    elif action.startswith("sch_day_"):
        target_date = action.replace("sch_day_", "")
        try:
            d_obj = datetime.strptime(target_date, "%Y-%m-%d")
            DAYS_OF_WEEK = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            title = f"{DAYS_OF_WEEK[d_obj.weekday()]}, {d_obj.strftime('%d.%m.%Y')}"
        except Exception:
            title = target_date

    await c.answer("⏳ Загрузка...")
    
    try:
        # Передаем target_date опорного дня и флаг is_week, гарантирующий выдачу всех учебных дней недели
        lessons, changes = await get_schedule(user_group, target_date=target_date, is_week=is_week)
        text = format_schedule_text(title, lessons, is_week, datetime.now(LOCAL_TZ))
        
        # Генерация навигации под расписанием
        kb = get_schedule_nav_keyboard(action) if is_week else InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=action)],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
        ])
        
        try:
            await c.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.error(f"Ошибка редактирования: {e}")
                await c.message.answer(text, reply_markup=kb, parse_mode="HTML")
                
    except Exception as e:
        logger.error(f"Schedule error: {e}")
        await c.message.answer("❌ Ошибка загрузки расписания", reply_markup=get_main_keyboard(c.from_user.id))

# --- ФОЛБЭК ДЛЯ НЕИЗВЕСТНЫХ СООБЩЕНИЙ ---

@router.message(F.text & ~F.text.startswith('/'))
async def handle_text_message(m: types.Message, state: FSMContext):
    if await state.get_state():
        return

    kb = get_main_keyboard(m.chat.id)
    await m.answer(
        "❓ <b>Я не понял команду.</b>\nНажми /start или выбери кнопку в меню 👇",
        reply_markup=kb,
        parse_mode="HTML"
    )
    logger.info(f"User {m.from_user.id} sent unknown text: '{m.text}'")

@router.message(F.text & F.text.startswith('/'))
async def handle_unknown_command(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("❓ <b>Такой команды нет.</b>\nИспользуй /start", parse_mode="HTML")
    logger.info(f"User {m.from_user.id} sent unknown command: '{m.text}'")