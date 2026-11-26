from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_IDS, PRIMARY_ENGINE, TIMEZONE
from database import (
    cancel_notify,
    get_user_group,
    get_user_settings,
    increment_request_count,
    log_action,
    save_user,
    set_user_group,
    update_notify_settings,
)
from engines import fetch_group_schedule, get_index, now_local, week_monday
from utils.formatter import format_schedule, split_message

router = Router()

DAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]
DAYS_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
GROUPS_PER_PAGE = 24


def _group_status(settings: dict) -> str:
    if not settings:
        return "🔕 Отключено"
    if settings["notify_time"]:
        return f"🕐 Ежедневно в {settings['notify_time']}"
    if settings["notify_type"] == "offset" and settings["notify_offset"] > 0:
        return f"⏳ За {settings['notify_offset']} ч. до пары"
    return "🔕 Отключено"


def get_main_keyboard(chat_id: int | None = None) -> InlineKeyboardMarkup:
    today = now_local()
    monday = today - timedelta(days=today.weekday())

    row1, row2 = [], []
    for index in range(6):
        day = monday + timedelta(days=index)
        text = DAYS_SHORT[index]
        if index == today.weekday() and today.weekday() < 6:
            text = f"• {text} •"
        button = InlineKeyboardButton(
            text=text, callback_data=f"day:{day.strftime('%Y-%m-%d')}"
        )
        (row1 if index < 3 else row2).append(button)

    rows = [
        row1,
        row2,
        [
            InlineKeyboardButton(text="📆 Неделя", callback_data="week"),
            InlineKeyboardButton(text="⏰ Уведы", callback_data="notify_menu"),
        ],
        [InlineKeyboardButton(text="🔄 Сменить группу", callback_data="change_group")],
    ]

    footer = [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")]
    if chat_id in ADMIN_IDS:
        footer.append(InlineKeyboardButton(text="👑 Админ", callback_data="admin_panel"))
    rows.append(footer)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_notify_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏰ По времени", callback_data="notify_type_time")],
            [InlineKeyboardButton(text="⏳ За время до", callback_data="notify_type_offset")],
            [InlineKeyboardButton(text="🗑 Отключить", callback_data="notify_delete")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
        ]
    )


def get_notify_time_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🕐 08:00", callback_data="notify_time_08:00")],
            [InlineKeyboardButton(text="🕐 08:30", callback_data="notify_time_08:30")],
            [InlineKeyboardButton(text="🕐 09:00", callback_data="notify_time_09:00")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="notify_menu")],
        ]
    )


def get_notify_offset_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏳ За 1 час", callback_data="notify_offset_1")],
            [InlineKeyboardButton(text="⏳ За 2 часа", callback_data="notify_offset_2")],
            [InlineKeyboardButton(text="⏳ За 3 часа", callback_data="notify_offset_3")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="notify_menu")],
        ]
    )


def get_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]]
    )


async def get_buildings_keyboard() -> InlineKeyboardMarkup:
    index = await get_index(PRIMARY_ENGINE)
    rows = [
        [InlineKeyboardButton(text=f"🏢 {building.name}", callback_data=f"bld:{building.id}")]
        for building in index.buildings
    ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def get_groups_keyboard(building_id: str, page: int) -> InlineKeyboardMarkup:
    index = await get_index(PRIMARY_ENGINE)
    building = next((b for b in index.buildings if b.id == building_id), None)
    if building is None:
        return await get_buildings_keyboard()

    start = page * GROUPS_PER_PAGE
    chunk = building.groups[start : start + GROUPS_PER_PAGE]
    rows = [
        [InlineKeyboardButton(text=group, callback_data=f"grp:{building_id}:{group}")]
        for group in chunk
    ]

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"grpnav:{building_id}:{page - 1}")
        )
    if start + GROUPS_PER_PAGE < len(building.groups):
        nav.append(
            InlineKeyboardButton(text="Вперед ▶️", callback_data=f"grpnav:{building_id}:{page + 1}")
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 Корпуса", callback_data="change_group")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_group_menu(target: types.Message | types.CallbackQuery, chat_id: int) -> None:
    group = await get_user_group(chat_id)
    keyboard = get_main_keyboard(chat_id)
    if group is None:
        keyboard = await get_buildings_keyboard()
        text = "👋 <b>Выбери свой корпус</b>, а затем группу:"
    else:
        text = f"📚 <b>Группа: {group}</b>\n\nВыбери день 👇"

    if isinstance(target, types.CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            return
        except Exception:
            target = target.message
    await target.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await save_user(message.chat.id, message.from_user.username)
    await log_action(message.chat.id, "start")
    await _show_group_menu(message, message.chat.id)


@router.message(Command("menu"))
async def cmd_menu(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await _show_group_menu(message, message.chat.id)


@router.callback_query(F.data == "main_menu")
async def handle_main_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_group_menu(callback.message, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "change_group")
async def handle_change_group(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    keyboard = await get_buildings_keyboard()
    try:
        await callback.message.edit_text(
            "🏢 <b>Выбери корпус</b>", reply_markup=keyboard, parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("bld:"))
async def handle_building(callback: types.CallbackQuery) -> None:
    building_id = callback.data.split(":", 1)[1]
    index = await get_index(PRIMARY_ENGINE)
    building = next((b for b in index.buildings if b.id == building_id), None)
    title = building.name if building else "корпус"
    try:
        await callback.message.edit_text(
            f"🏢 <b>{title}</b>\n\nВыбери группу:",
            reply_markup=await get_groups_keyboard(building_id, 0),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("grpnav:"))
async def handle_groups_nav(callback: types.CallbackQuery) -> None:
    _, building_id, page = callback.data.split(":")
    try:
        await callback.message.edit_text(
            "Выбери группу:",
            reply_markup=await get_groups_keyboard(building_id, int(page)),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("grp:"))
async def handle_group_select(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    _, building_id, group = callback.data.split(":", 2)
    await set_user_group(callback.from_user.id, group)
    await log_action(callback.from_user.id, f"group:{group}")
    await _show_group_menu(callback.message, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "notify_menu")
async def handle_notify_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    settings = await get_user_settings(callback.from_user.id)
    text = (
        f"⏰ <b>Настройки уведомлений</b>\n\n"
        f"Статус: {_group_status(settings or {})}\n\nВыберите тип:"
    )
    try:
        await callback.message.edit_text(
            text, reply_markup=get_notify_menu_kb(), parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "notify_type_time")
async def handle_notify_type_time(callback: types.CallbackQuery) -> None:
    try:
        await callback.message.edit_text(
            "⏰ <b>Уведомлять в определенное время</b>",
            reply_markup=get_notify_time_kb(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "notify_type_offset")
async def handle_notify_type_offset(callback: types.CallbackQuery) -> None:
    try:
        await callback.message.edit_text(
            "⏳ <b>Уведомлять за время до первой пары</b>",
            reply_markup=get_notify_offset_kb(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("notify_time_"))
async def handle_notify_set_time(callback: types.CallbackQuery) -> None:
    value = callback.data.replace("notify_time_", "")
    await update_notify_settings(callback.from_user.id, time=value, notify_type="time", offset=0)
    await log_action(callback.from_user.id, f"notify:{value}")
    try:
        await callback.message.edit_text(
            f"✅ Установлено на <b>{value}</b>",
            reply_markup=get_notify_menu_kb(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("notify_offset_"))
async def handle_notify_set_offset(callback: types.CallbackQuery) -> None:
    offset = int(callback.data.replace("notify_offset_", ""))
    await update_notify_settings(callback.from_user.id, time=None, notify_type="offset", offset=offset)
    await log_action(callback.from_user.id, f"notify_offset:{offset}")
    try:
        await callback.message.edit_text(
            f"✅ Установлено: за <b>{offset} ч.</b> до пары",
            reply_markup=get_notify_menu_kb(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "notify_delete")
async def handle_notify_delete(callback: types.CallbackQuery) -> None:
    await cancel_notify(callback.from_user.id)
    await log_action(callback.from_user.id, "notify_off")
    try:
        await callback.message.edit_text(
            "🔕 <b>Уведомления отключены</b>",
            reply_markup=get_notify_menu_kb(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "help")
async def handle_help(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "Бот показывает расписание занятий колледжа.\n"
        "Кнопки внизу — навигация по дням и неделе.\n\n"
        "/start — главное меню\n"
        "/menu — расписание"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


async def _render_schedule(callback: types.CallbackQuery, action: str) -> None:
    group = await get_user_group(callback.from_user.id)
    if group is None:
        await _show_group_menu(callback.message, callback.from_user.id)
        await callback.answer()
        return

    await increment_request_count(callback.from_user.id)
    await log_action(callback.from_user.id, action)

    today = now_local().date()
    is_week = action == "week"
    target_date = None if is_week else action.split(":", 1)[1]
    monday = week_monday(today)

    if is_week:
        title = f"Неделя ({monday[8:10]}.{monday[5:7]} — {(today + timedelta(days=5 - today.weekday())).strftime('%d.%m')})"
    else:
        try:
            day = datetime.strptime(target_date, "%Y-%m-%d")
            title = f"{DAYS_FULL[day.weekday()]}, {day.strftime('%d.%m.%Y')}"
        except ValueError:
            title = target_date

    await callback.answer("⏳ Загрузка...")

    try:
        lessons, _ = await fetch_group_schedule(group, monday)
        if not is_week:
            lessons = [lesson for lesson in lessons if lesson.date == target_date]

        text = format_schedule(title, lessons, is_week, datetime.now(TIMEZONE))
        chunks = split_message(text)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data=action)],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
            ]
        )
        try:
            await callback.message.edit_text(chunks[0], reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            await callback.message.answer(chunks[0], reply_markup=keyboard, parse_mode="HTML")
        for chunk in chunks[1:]:
            await callback.message.answer(chunk, parse_mode="HTML")
    except Exception as error:
        await callback.message.answer(
            f"❌ Не удалось загрузить расписание.\n<code>{error}</code>",
            reply_markup=get_main_keyboard(callback.from_user.id),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "week")
async def handle_week(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_schedule(callback, "week")


@router.callback_query(F.data.startswith("day:"))
async def handle_day(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_schedule(callback, callback.data)


@router.message(lambda message: bool(message.text) and not message.text.startswith("/"))
async def handle_text(message: types.Message, state: FSMContext) -> None:
    if await state.get_state():
        return
    await message.answer(
        "❓ <b>Не понял сообщение.</b>\nНажми /start или выбери кнопку в меню 👇",
        reply_markup=get_main_keyboard(message.chat.id),
        parse_mode="HTML",
    )


@router.callback_query()
async def handle_stale_callback(callback: types.CallbackQuery) -> None:
    await callback.answer("Кнопка устарела, открой /start", show_alert=True)
