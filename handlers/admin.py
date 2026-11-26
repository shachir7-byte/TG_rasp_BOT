from __future__ import annotations

import asyncio
import logging
from html import escape

import psutil
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_IDS
from database import (
    get_admin_stats,
    get_all_users_chat_ids,
    get_selected_engine,
    get_user_count,
    log_action,
    set_selected_engine,
)
from engines import ENGINES, get_engine, health_check, last_responded

logger = logging.getLogger(__name__)
router = Router()


class AdminStates(StatesGroup):
    broadcast_wait = State()


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _engine_role(engine) -> str:
    return "Основной" if engine.primary else "Запасной"


def get_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="🧪 Тест", callback_data="admin_test")],
            [InlineKeyboardButton(text="🔄 Сменить движок", callback_data="admin_engines")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
        ]
    )


async def _panel_text() -> str:
    selected = await get_selected_engine()
    selected_engine = get_engine(selected)

    last = last_responded()
    last_key = last.get("engine")
    if last_key:
        last_engine = get_engine(str(last_key))
        icon = "🟢" if last.get("ok") else "🔴"
        last_line = f"{icon} {_engine_role(last_engine)} {last_engine.key} ({last_engine.name})"
    else:
        last_line = "⚪️ запросов ещё не было"

    return (
        "👑 <b>Админ-панель управления</b>\n"
        "──────────────────────────\n"
        f"📡 Выбранный движок: {selected} ({selected_engine.name} — "
        f"{_engine_role(selected_engine)})\n"
        f"🎯 Последний ответ от: {last_line}\n\n"
        "Выберите действие ниже:"
    )


async def _show_panel(callback: types.CallbackQuery) -> None:
    try:
        await callback.message.edit_text(
            await _panel_text(), reply_markup=get_admin_kb(), parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: types.CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("🔐 Доступ запрещен", show_alert=True)
        return
    await _show_panel(callback)


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("🔐 Доступ запрещен", show_alert=True)
        return

    users = await get_admin_stats()
    total = len(users)
    total_requests = sum(user["request_count"] for user in users)
    lines = [
        "📊 <b>Статистика</b>\n",
        f"👥 Всего пользователей: <b>{total}</b>",
        f"📈 Всего запросов: <b>{total_requests}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for user in users[:30]:
        name = f"@{user['username']}" if user["username"] else f"User {user['chat_id']}"
        group = user["user_group"] or "—"
        lines.append(
            f"🆔 <code>{user['chat_id']}</code> <b>{escape(name)}</b> ({escape(group)})\n"
            f"   └ Запросов: {user['request_count']}"
        )

    text = "\n".join(lines)
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_kb(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=get_admin_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_engines")
async def admin_engines(callback: types.CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("🔐 Доступ запрещен", show_alert=True)
        return

    await callback.answer("⏳ Проверяю движки...")
    health = await health_check()
    selected = await get_selected_engine()

    rows = []
    for key, engine in ENGINES.items():
        icon = "🟢" if health.get(key) else "🔴"
        mark = " ✅" if key == selected else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon} {key} ({engine.name}) — {_engine_role(engine)}{mark}",
                    callback_data=f"engine_set:{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])

    try:
        await callback.message.edit_text(
            "🔄 <b>Сменить движок</b>\n\nНажми на вариант:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("engine_set:"))
async def admin_engine_set(callback: types.CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("🔐 Доступ запрещен", show_alert=True)
        return

    engine_key = callback.data.split(":", 1)[1]
    if engine_key not in ENGINES:
        await callback.answer("Неизвестный движок", show_alert=True)
        return

    await set_selected_engine(engine_key)
    await log_action(callback.from_user.id, f"engine:{engine_key}")
    await _show_panel(callback)


@router.callback_query(F.data == "admin_test")
async def admin_test(callback: types.CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("🔐 Доступ запрещен", show_alert=True)
        return

    await callback.answer("🧪 Диагностика...")
    report = ["🧪 <b>Диагностика системы</b>", "──────────────────────────"]

    try:
        count = await get_user_count()
        report.append(f"🗄 <b>База данных:</b> 🟢 OK ({count} пользователей)")
    except Exception as error:
        report.append(f"🗄 <b>База данных:</b> 🔴 {error}")

    report.append("")
    report.append("🌐 <b>Движки:</b>")
    health = await health_check()
    selected = await get_selected_engine()
    for key, engine in ENGINES.items():
        icon = "🟢" if health.get(key) else "🔴"
        mark = " (выбран)" if key == selected else ""
        report.append(f"   {icon} {key} ({engine.name}) — {_engine_role(engine)}{mark}")

    last = last_responded()
    if last.get("engine"):
        last_engine = get_engine(str(last["engine"]))
        icon = "🟢" if last.get("ok") else "🔴"
        report.append(
            f"🎯 Последний ответ: {icon} {last_engine.key} ({last_engine.name}) "
            f"в {last.get('at')}"
        )

    try:
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        cpu_percent = psutil.cpu_percent(interval=0.1)
        report.append("")
        report.append("💻 <b>Хост:</b>")
        report.append(f"   ├ Загрузка CPU: <code>{cpu_percent}%</code>")
        report.append(f"   └ Память процесса: <code>{memory_mb:.1f} MB</code>")
    except Exception as error:
        report.append(f"💻 <b>Хост:</b> 🔴 {error}")

    text = "\n".join(report)
    await callback.message.answer(text, parse_mode="HTML", reply_markup=get_admin_kb())


@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("🔐 Доступ запрещен", show_alert=True)
        return

    await state.set_state(AdminStates.broadcast_wait)
    await log_action(callback.from_user.id, "broadcast_start")
    text = (
        "📢 <b>Режим рассылки</b>\n\n"
        "Напиши текст — отправлю всем.\n"
        "Формат <code>ID: текст</code> — личное сообщение.\n"
        "/cancel — выйти."
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_kb())
    except Exception:
        pass
    await callback.answer()


@router.message(AdminStates.broadcast_wait)
async def process_broadcast(message: types.Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return

    text = (message.text or "").strip()
    if not text:
        return

    if text.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Рассылка отменена.", reply_markup=get_admin_kb())
        return

    if text.startswith("/"):
        await state.clear()
        await message.answer("Рассылка отменена (введена команда).", reply_markup=get_admin_kb())
        return

    target_id = None
    message_text = text
    if ":" in text:
        head, tail = text.split(":", 1)
        if head.strip().isdigit():
            target_id = int(head.strip())
            message_text = tail.strip()

    if target_id:
        try:
            await message.bot.send_message(target_id, message_text, parse_mode="HTML")
            await message.answer(f"✅ Отправлено пользователю {target_id}")
            await log_action(message.from_user.id, f"broadcast_to:{target_id}")
        except Exception as error:
            await message.answer(f"❌ Ошибка отправки: {error}")
    else:
        chat_ids = await get_all_users_chat_ids()
        success, failed = 0, 0
        await message.answer(f"📢 Рассылка для {len(chat_ids)} пользователей...")

        semaphore = asyncio.Semaphore(8)

        async def send(chat_id: int) -> None:
            nonlocal success, failed
            if chat_id in ADMIN_IDS:
                return
            async with semaphore:
                try:
                    await message.bot.send_message(chat_id, message_text, parse_mode="HTML")
                    success += 1
                except Exception:
                    failed += 1

        await asyncio.gather(*(send(chat_id) for chat_id in chat_ids))
        await message.answer(f"✅ Успешно: {success}\n❌ Ошибок: {failed}")
        await log_action(message.from_user.id, f"broadcast_all:{success}:{failed}")

    await state.clear()
