import logging
import os
import signal
import psutil
import asyncio
import time
from datetime import datetime, timedelta, timezone
from aiogram import Router, F, types
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_ID, LOCAL_TIMEZONE_OFFSET, START_TIME, PROXY_URL
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
    cur_ver = api_client.CURRENT_API_VERSION
    other_ver = 1 if cur_ver == 2 else 2
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton(text="🧪 Тест и API", callback_data="admin_test")
        ],
        [
            InlineKeyboardButton(text=f"🔀 Сменить API на v{other_ver}", callback_data="admin_switch_api"),
            InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")
        ],
        [InlineKeyboardButton(text="🔄 Перезапуск бота", callback_data="admin_restart_confirm")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])

@router.callback_query(F.data == "admin_panel")
async def admin_panel(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("🔐 Доступ запрещен", show_alert=True)
    await state.clear()
    status_info = api_client.get_api_status_info()
    text = (
        "👑 <b>Админ-панель управления</b>\n"
        "──────────────────────────\n"
        f"📡 <b>Выбранный движок:</b> <code>{status_info['configured_name']}</code>\n"
        f"🎯 <b>Последний ответ от:</b> {status_info['last_source']}\n\n"
        "Выберите действие ниже:"
    )
    try:
        await c.message.edit_text(text, reply_markup=get_admin_kb(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "admin_switch_api")
async def admin_switch_api(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return
    new_ver = 1 if api_client.CURRENT_API_VERSION == 2 else 2
    api_client.set_active_api_version(new_ver)
    await log_action(c.from_user.id, f"admin_switch_api_v{new_ver}")
    await c.answer(f"✅ Приоритетный API переключен на v{new_ver}! Кэш очищен.", show_alert=True)
    await admin_test(c)

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
    try:
        await c.answer("🧪 Проверяю оба API и ресурсы сервера...", show_alert=False)
    except Exception:
        pass
    
    report = [
        "🧪 <b>System & Dual-API Diagnostic</b>",
        "──────────────────────────"
    ]

    # 1. База данных
    try:
        count = await get_user_count()
        report.append(f"🗄 <b>База данных:</b> 🟢 OK (WAL) | Юзеров: <code>{count}</code>")
    except Exception as e:
        report.append(f"🗄 <b>База данных:</b> 🔴 ERROR (<code>{e}</code>)")

    # 2. Статус Основного и Резервного API
    status_info = api_client.get_api_status_info()
    health = await api_client.check_both_apis_health()
    
    v2_h = health["v2"]
    v1_h = health["v1"]
    v2_icon = "🟢" if v2_h["ok"] else f"🔴 ({v2_h['error']})"
    v1_icon = "🟢" if v1_h["ok"] else f"🔴 ({v1_h['error']})"

    report.append("")
    report.append("🌐 <b>Состояние серверов расписания:</b>")
    report.append(f"   ├ <b>Приоритет:</b> <code>API v{status_info['configured_version']}</code>")
    report.append(f"   ├ <b>Основной (v2 sielom-web):</b> {v2_icon} <code>{v2_h['ms']}мс</code>")
    report.append(f"   ├ <b>Резервный (v1 sielom.ru):</b> {v1_icon} <code>{v1_h['ms']}мс</code>")
    report.append(f"   ├ <b>Реально работает на:</b> {status_info['last_source']}")
    report.append(f"   └ <b>Последний сбой:</b> <code>{status_info['last_error']}</code>")

    # 3. Тестовая выгрузка расписания
    try:
        start_t = time.time()
        data, changes = await api_client.get_schedule('is', datetime.now(TZ).strftime("%Y-%m-%d"), is_week=True)
        api_time = (time.time() - start_t) * 1000
        if data is not None:
            report.append(f"📚 <b>Тест пар (ИС):</b> 🟢 <code>{len(data)} пар</code> ({api_time:.0f}мс)")
        else:
            report.append("📚 <b>Тест пар (ИС):</b> 🔴 <code>Не удалось получить</code>")
    except Exception as e:
        report.append(f"📚 <b>Тест пар:</b> 🔴 <code>{e}</code>")

    # 4. Ресурсы сервера Ubuntu и Память
    try:
        process = psutil.Process()
        bot_mem_mb = process.memory_info().rss / 1024 / 1024
        sys_mem = psutil.virtual_memory()
        cpu_percent = psutil.cpu_percent(interval=0.1)
        
        uptime_sec = int(time.time() - START_TIME)
        days, rem = divmod(uptime_sec, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        uptime_str = f"{days}д {hours}ч {minutes}м" if days > 0 else f"{hours}ч {minutes}м {seconds}с"
        
        report.append("")
        report.append("💻 <b>Ресурсы хоста (Ubuntu):</b>")
        report.append(f"   ├ CPU Load: <code>{cpu_percent}%</code>")
        report.append(f"   ├ RAM Бота: <code>{bot_mem_mb:.1f} MB</code>")
        report.append(f"   ├ RAM Системы: <code>{sys_mem.percent}% ({sys_mem.used // (1024**2)} / {sys_mem.total // (1024**2)} MB)</code>")
        report.append(f"   └ Uptime бота: <code>{uptime_str}</code>")
    except Exception as e:
        report.append(f"💻 <b>Система:</b> 🔴 ERROR ({e})")

    # 5. Инфо о прокси (главная причина потери связи с ТГ при сне ПК)
    report.append("")
    report.append("🛰 <b>Канал связи с Telegram:</b>")
    if PROXY_URL:
        report.append(f"   ├ Режим: 🟠 <b>Через прокси ПК</b> (<code>{PROXY_URL}</code>)")
        report.append("   └ <i>Если ПК уснет или выключится VPN, бот ждет пробуждения ПК.</i>")
    else:
        report.append("   └ Режим: 🟢 <b>Прямое подключение</b> (без прокси)")

    final_text = "\n".join(report)
    try:
        await c.message.edit_text(final_text, parse_mode="HTML", reply_markup=get_admin_kb())
    except Exception:
        await c.message.answer(final_text, parse_mode="HTML", reply_markup=get_admin_kb())

@router.callback_query(F.data == "admin_restart_confirm")
async def admin_restart_confirm(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, перезапустить", callback_data="admin_restart_execute"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")
        ]
    ])
    text = (
        "⚠️ <b>Подтверждение перезапуска</b>\n\n"
        "Вы уверены, что хотите перезапустить бота?\n"
        "• На сервере Ubuntu служба <code>systemd</code> автоматически поднимет его через 2–3 секунды.\n"
        "• При ручном запуске на ПК процесс просто завершится."
    )
    try:
        await c.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "admin_restart_execute")
async def admin_restart_execute(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return
    await log_action(c.from_user.id, "admin_bot_restart")
    await c.answer("🔄 Отправляю сигнал перезапуска...", show_alert=False)
    try:
        await c.message.edit_text(
            "🔄 <b>Перезапуск инициирован...</b>\n"
            "Бот сейчас перезагрузится и пришлет уведомление о старте.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    async def _delayed_shutdown():
        await asyncio.sleep(1.0)
        logger.warning("🔄 Перезапуск бота по команде администратора из Telegram (SIGTERM)")
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_delayed_shutdown())

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