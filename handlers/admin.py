import logging
import psutil
from aiogram import Router, F, types
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import ADMIN_ID
from database import get_full_admin_stats, clear_stats, get_user_count
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
    except: pass
    await c.answer()

@router.callback_query(F.data == "admin_stats")
async def admin_stats(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    text = await get_full_admin_stats()
    try:
        await c.message.edit_text(text, reply_markup=get_admin_kb(), parse_mode="HTML")
    except:
        await c.message.answer(text, parse_mode="HTML")
    await c.answer()

@router.callback_query(F.data == "admin_test")
async def admin_test(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    await c.answer("🧪 Диагностика...", show_alert=False)
    report = ["🧪 <b>Отчет</b>\n"]

    # БД
    try:
        count = await get_user_count()
        report.append(f"✅ <b>БД:</b> OK ({count} юзеров)")
    except Exception as e:
        report.append(f"❌ <b>БД:</b> {e}")

    # API
    try:
        start_time = datetime.now().timestamp()
        data, _ = await get_schedule('is', datetime.now(TZ).strftime("%Y-%m-%d"))
        api_time = (datetime.now().timestamp() - start_time) * 1000
        report.append(f"✅ <b>API:</b> OK ({len(data)} пар) [{api_time:.0f}мс]")
    except Exception as e:
        report.append(f"❌ <b>API:</b> {e}")

    # Система
    try:
        process = psutil.Process()
        mem_mb = process.memory_info().rss / 1024 / 1024
        cpu_percent = psutil.cpu_percent(interval=0.1)
        report.append(f"💻 <b>CPU:</b> {cpu_percent}% | <b>RAM:</b> {mem_mb:.1f} MB")
    except Exception as e:
        report.append(f"❌ <b>Система:</b> {e}")

    await c.message.answer("\n".join(report), parse_mode="HTML", reply_markup=get_admin_kb())

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.broadcast_wait)
    await c.message.edit_text("📢 <b>Режим рассылки</b>\nПиши текст или ID: Текст", parse_mode="HTML", reply_markup=get_admin_kb())
    await c.answer()