import logging
import psutil
import asyncio
import time
from aiogram import Router, F, types
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import ADMIN_ID
from database import (
    get_full_admin_stats, clear_stats, get_user_count,
    get_all_users_chat_ids, log_action
)
from utils.api_client import get_schedule, GLOBAL_SESSION
from datetime import datetime, timedelta, timezone
from config import LOCAL_TIMEZONE_OFFSET

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
async def admin_panel(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("🔐 Доступ запрещен", show_alert=True)
    try:
        await c.message.edit_text("👑 <b>Админ-панель</b>", reply_markup=get_admin_kb(), parse_mode="HTML")
    except Exception: pass
    await c.answer()

@router.callback_query(F.data == "admin_stats")
async def admin_stats(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    text = await get_full_admin_stats()
    try:
        await c.message.edit_text(text, reply_markup=get_admin_kb(), parse_mode="HTML")
    except Exception:
        await c.message.answer(text, parse_mode="HTML")
    await c.answer()

@router.callback_query(F.data == "admin_test")
async def admin_test(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    await c.answer("🧪 Диагностика...", show_alert=False)
    
    report = []
    report.append("🧪 <b>System Diagnostic Report</b>")
    report.append("──────────────────────────")

    # 1. База данных
    try:
        count = await get_user_count()
        report.append(f"🗄 <b>База данных:</b> 🟢 OK")
        report.append(f"👥 <b>Пользователей:</b> <code>{count}</code>")
    except Exception as e:
        report.append(f"🗄 <b>База данных:</b> 🔴 ERROR")
        report.append(f"   └ <code>{e}</code>")

    # 2. API и Кэш
    try:
        start_time = time.time()
        data, changes = await get_schedule('is', datetime.now(TZ).strftime("%Y-%m-%d"))
        api_time = (time.time() - start_time) * 1000
        cache_status = "(кэш)" if not changes else "(обновлено)"
        status_icon = "🟢" if len(data) > 0 else "🟡"
        
        report.append(f"🌐 <b>API sielom.ru:</b> {status_icon} OK")
        report.append(f"   └ Пар: <code>{len(data)}</code> {cache_status}")
        report.append(f"   └ Response: <code>{api_time:.0f}мс</code>")
    except Exception as e:
        report.append(f"🌐 <b>API sielom.ru:</b> 🔴 ERROR")
        report.append(f"   └ <code>{e}</code>")

    # 3. Система (Ресурсы ПК)
    try:
        process = psutil.Process()
        mem_mb = process.memory_info().rss / 1024 / 1024
        cpu_percent = psutil.cpu_percent(interval=0.1)
        
        report.append("")
        report.append("💻 <b>Хост (Твой ПК):</b>")
        report.append(f"   ├ CPU Load: <code>{cpu_percent}%</code>")
        report.append(f"   └ RAM Usage: <code>{mem_mb:.1f} MB</code>")
    except Exception as e:
        report.append(f"💻 <b>Система:</b> 🔴 ERROR ({e})")

    # 4. Пинг (С проверкой сессии)
    report.append("")
    report.append("🛰 <b>Network:</b>")
    try:
        if GLOBAL_SESSION is None:
            report.append("   └ Ping API: <code>Сессия не инициализирована</code>")
        else:
            start_ping = time.time()
            # Пингуем главную или эндпоинт недель
            async with GLOBAL_SESSION.get("https://sielom.ru/schedule/api/weeks", timeout=5) as resp:
                ping_ms = (time.time() - start_ping) * 1000
                report.append(f"   └ Ping API: <code>{ping_ms:.0f}мс</code>")
    except Exception as e:
        report.append(f"   └ Ping API: 🔴 <code>{e}</code>")

    report.append("")
    report.append("✅ <b>Статус:</b> Бот готов к работе")

    final_text = "\n".join(report)
    await c.message.answer(final_text, parse_mode="HTML", reply_markup=get_admin_kb())

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.broadcast_wait)
    await log_action(c.from_user.id, "start_broadcast")
    text = (
        "📢 <b>Режим рассылки</b>\n\n"
        "1. <b>Всем:</b> просто напиши текст сообщения.\n"
        "2. <b>Лично:</b> напиши `ID: Текст` (например: `123456: Привет`).\n\n"
        "Напиши /cancel для выхода."
    )
    try:
        await c.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_kb())
    except Exception: pass
    await c.answer()

@router.message(AdminStates.broadcast_wait)
async def process_broadcast(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID:
        return

    text = m.text.strip()

    if text.lower() in ['/cancel', 'отмена']:
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
            await m.answer(f"✅ Отправлено пользователю {target_id}")
            await log_action(m.from_user.id, f"broadcast_to_{target_id}")
        except Exception as e:
            await m.answer(f"❌ Ошибка отправки: {e}")
    else:
        users = await get_all_users_chat_ids()
        success, failed = 0, 0
        await m.answer(f"📢 Начало рассылки для {len(users)} пользователей...")

        for uid in users:
            if uid == ADMIN_ID: continue
            try:
                await m.bot.send_message(uid, message_text, parse_mode="HTML")
                success += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)

        await m.answer(f"✅ Успешно: {success}\n❌ Ошибок: {failed}")
        await log_action(m.from_user.id, f"broadcast_all_{success}_{failed}")

    await state.clear()