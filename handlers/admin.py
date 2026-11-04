import logging
import psutil
import asyncio
import time
from datetime import datetime, timedelta, timezone
from aiogram import Router, F, types
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_ID, LOCAL_TIMEZONE_OFFSET, API_VERSION, START_TIME
from database import (
    get_full_admin_stats, get_user_count,
    get_all_users_chat_ids, log_action
)
import utils.api_client as api_client

logger = logging.getLogger(__name__)
router = Router()
TZ = timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET))

class AdminStates(StatesGroup):
    broadcast_wait = State()

def get_admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🧪 Тест", callback_data="admin_test")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])

@router.callback_query(F.data == "admin_panel")
async def admin_panel(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("🔐 Доступ запрещен", show_alert=True)
    await state.clear()
    try:
        await c.message.edit_text("👑 <b>Админ-панель</b>\nВыберите действие:", reply_markup=get_admin_kb(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "admin_stats")
async def admin_stats(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return
    text = await get_full_admin_stats()
    try:
        await c.message.edit_text(text, reply_markup=get_admin_kb(), parse_mode="HTML")
    except Exception:
        await c.message.answer(text, reply_markup=get_admin_kb(), parse_mode="HTML")
    await c.answer()

@router.callback_query(F.data == "admin_test")
async def admin_test(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return
    await c.answer("🧪 Собираю диагностику...", show_alert=False)
    
    api_domain = "sielom-web.ru (v2)" if API_VERSION == 2 else "sielom.ru (v1)"
    report = [
        "🧪 <b>System Diagnostic Report</b>",
        "──────────────────────────"
    ]

    # 1. База данных
    try:
        count = await get_user_count()
        report.append("🗄 <b>База данных:</b> 🟢 OK (WAL)")
        report.append(f"👥 <b>Пользователей:</b> <code>{count}</code>")
    except Exception as e:
        report.append("🗄 <b>База данных:</b> 🔴 ERROR")
        report.append(f"   └ <code>{e}</code>")

    # 2. API и Кэш
    try:
        start_t = time.time()
        data, changes = await api_client.get_schedule('is', datetime.now(TZ).strftime("%Y-%m-%d"), is_week=True)
        api_time = (time.time() - start_t) * 1000
        
        if data is None:
            report.append(f"🌐 <b>API {api_domain}:</b> 🔴 СБОЙ СЕТИ")
            report.append("   └ Пар: <code>Нет ответа</code>")
        else:
            cache_status = "(кэш)" if not changes else "(обновлено)"
            status_icon = "🟢" if len(data) > 0 else "🟡"
            report.append(f"🌐 <b>API {api_domain}:</b> {status_icon} OK")
            report.append(f"   └ Пар на неделе: <code>{len(data)}</code> {cache_status}")
            report.append(f"   └ Response: <code>{api_time:.0f}мс</code>")
    except Exception as e:
        report.append(f"🌐 <b>API {api_domain}:</b> 🔴 ERROR")
        report.append(f"   └ <code>{e}</code>")

    # 3. Система (Ресурсы сервера)
    try:
        process = psutil.Process()
        mem_mb = process.memory_info().rss / 1024 / 1024
        cpu_percent = psutil.cpu_percent(interval=0.1)
        
        uptime_sec = int(time.time() - START_TIME)
        hours, rem = divmod(uptime_sec, 3600)
        minutes, seconds = divmod(rem, 60)
        
        report.append("")
        report.append("💻 <b>Сервер (Ubuntu):</b>")
        report.append(f"   ├ CPU Load: <code>{cpu_percent}%</code>")
        report.append(f"   ├ RAM Бота: <code>{mem_mb:.1f} MB</code>")
        report.append(f"   └ Uptime: <code>{hours}ч {minutes}м {seconds}с</code>")
    except Exception as e:
        report.append(f"💻 <b>Система:</b> 🔴 ERROR ({e})")

    # 4. Проверка HTTP-сессии
    report.append("")
    report.append("🛰 <b>Network:</b>")
    if api_client.GLOBAL_SESSION is None:
        report.append("   └ Сессия API: 🔴 <code>Не инициализирована</code>")
    else:
        report.append("   └ Сессия API: 🟢 <code>Активна и готова</code>")

    report.append("")
    report.append("✅ <b>Статус:</b> Бот работает штатно")

    final_text = "\n".join(report)
    try:
        await c.message.edit_text(final_text, parse_mode="HTML", reply_markup=get_admin_kb())
    except Exception:
        await c.message.answer(final_text, parse_mode="HTML", reply_markup=get_admin_kb())

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.broadcast_wait)
    await log_action(c.from_user.id, "start_broadcast")
    text = (
        "📢 <b>Режим рассылки</b>\n\n"
        "1. <b>Всем:</b> просто напиши текст сообщения.\n"
        "2. <b>Лично:</b> напиши <code>ID: Текст</code> (например: <code>123456: Привет</code>).\n\n"
        "Напиши /cancel для выхода."
    )
    try:
        await c.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_kb())
    except Exception:
        pass
    await c.answer()

@router.message(AdminStates.broadcast_wait)
async def process_broadcast(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID or not m.text:
        return

    text = m.text.strip()

    if text.lower() in ['/cancel', 'отмена', '/start']:
        await state.clear()
        await m.answer("❌ Рассылка отменена.", reply_markup=get_admin_kb())
        return

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
            await m.answer(f"✅ Отправлено пользователю <code>{target_id}</code>", parse_mode="HTML", reply_markup=get_admin_kb())
            await log_action(m.from_user.id, f"broadcast_to_{target_id}")
        except Exception as e:
            await m.answer(f"❌ Ошибка отправки: {e}", reply_markup=get_admin_kb())
    else:
        users = await get_all_users_chat_ids()
        success, failed = 0, 0
        status_msg = await m.answer(f"📢 Начало рассылки для {len(users)} пользователей...")

        for uid in users:
            if uid == ADMIN_ID:
                continue
            try:
                await m.bot.send_message(uid, message_text, parse_mode="HTML")
                success += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)

        await status_msg.edit_text(
            f"📢 <b>Рассылка завершена!</b>\n\n✅ Успешно: <code>{success}</code>\n❌ Ошибок: <code>{failed}</code>",
            parse_mode="HTML",
            reply_markup=get_admin_kb()
        )
        await log_action(m.from_user.id, f"broadcast_all_{success}_{failed}")

    await state.clear()