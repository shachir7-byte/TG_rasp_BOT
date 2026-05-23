import logging
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from datetime import datetime, timedelta, timezone
from config import LOCAL_TIMEZONE_OFFSET, ADMIN_ID
from database import get_user_group, increment_request_count, log_action, get_groups_config, save_user
from utils.api_client import get_schedule
from utils.formatter import format_schedule_text

logger = logging.getLogger(__name__)
router = Router()
LOCAL_TZ = timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET))

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

def get_notify_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏰ По времени", callback_data="notify_type_time")],
        [InlineKeyboardButton(text="⏳ За время до", callback_data="notify_type_offset")],
        [InlineKeyboardButton(text="🗑 Отключить", callback_data="notify_delete")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
    ])

def get_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
    ])

# --- ОБРАБОТЧИКИ (HANDLERS) ---

@router.message(Command("start"))
async def cmd_start(m: types.Message):
    await save_user(m.chat.id, m.from_user.username, 'is')
    await log_action(m.chat.id, "start")
    kb = get_main_keyboard(m.chat.id)
    await m.answer("👋 <b>Привет! Я бот-расписание</b>\n\nВыберите группу или день:", reply_markup=kb, parse_mode="HTML")

@router.message(Command("menu"))
async def cmd_menu(m: types.Message):
    group_key = await get_user_group(m.chat.id)
    groups = await get_groups_config()
    group_name = groups.get(group_key, {}).get('name', 'ИС')
    kb = get_main_keyboard(m.chat.id)
    await m.answer(f"📚 <b>Группа: {group_name}</b>\n\nВыбери день 👇", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("group_"))
async def handle_group_select(c: types.CallbackQuery):
    group_key = c.data.replace("group_", "")
    groups = await get_groups_config()
    group_name = groups.get(group_key, {}).get('name', 'Неизвестно')
    
    await save_user(c.from_user.id, c.from_user.username, group_key)
    await log_action(c.from_user.id, f"group_select_{group_key}")
    
    try:
        await c.message.edit_text(f"✅ <b>Группа: {group_name}</b>\n\nВыбери день 👇", reply_markup=get_main_keyboard(c.from_user.id), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "main_menu")
async def handle_main_menu(c: types.CallbackQuery):
    group_key = await get_user_group(c.from_user.id)
    groups = await get_groups_config()
    group_name = groups.get(group_key, {}).get('name', 'ИС')
    try:
        await c.message.edit_text(f"📚 <b>Группа: {group_name}</b>\n\nВыбери день 👇", reply_markup=get_main_keyboard(c.from_user.id), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "change_group")
async def handle_change_group(c: types.CallbackQuery):
    try:
        await c.message.edit_text("🔄 <b>Сменить группу</b>", reply_markup=get_group_select_keyboard(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "notify_menu")
async def handle_notify_menu(c: types.CallbackQuery):
    try:
        await c.message.edit_text("⏰ <b>Настройки уведомлений</b>\n\nЗдесь можно настроить время оповещения.", reply_markup=get_notify_menu_kb(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data == "help")
async def handle_help(c: types.CallbackQuery):
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "Этот бот показывает расписание занятий.\n"
        "Используй кнопки внизу для навигации.\n"
        "/start - Главное меню\n"
        "/menu - Показать расписание"
    )
    try:
        await c.message.edit_text(text, reply_markup=get_back_kb(), parse_mode="HTML")
    except Exception:
        pass
    await c.answer()

@router.callback_query(F.data.startswith("sch_"))
async def handle_schedule(c: types.CallbackQuery):
    action = c.data
    user_group = await get_user_group(c.from_user.id)
    await increment_request_count(c.from_user.id)
    await log_action(c.from_user.id, action)
    
    target_date = None
    title = ""
    is_week = False
    
    if action == "sch_week":
        is_week = True
        today = datetime.now(LOCAL_TZ)
        if today.weekday() == 6:
            start = today + timedelta(days=1)
            title = f"Неделя ({start.strftime('%d.%m')} - {(start+timedelta(days=5)).strftime('%d.%m')}) (След.)"
        else:
            start = today - timedelta(days=today.weekday())
            title = f"Неделя ({start.strftime('%d.%m')} - {(start+timedelta(days=5)).strftime('%d.%m')})"
            
    elif action.startswith("sch_day_"):
        target_date = action.replace("sch_day_", "")
        try:
            d_obj = datetime.strptime(target_date, "%Y-%m-%d")
            DAYS_OF_WEEK = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            title = f"{DAYS_OF_WEEK[d_obj.weekday()]}, {d_obj.strftime('%d.%m.%Y')}"
        except:
            title = target_date

    await c.answer("⏳ Загрузка...")
    
    try:
        lessons, changes = await get_schedule(user_group, target_date if not is_week else None)
        text = format_schedule_text(title, lessons, is_week, datetime.now(LOCAL_TZ))
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
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
        await c.message.answer("❌ Ошибка загрузки расписания", reply_markup=get_main_keyboard())

# --- ОБРАБОТЧИКИ ТЕКСТОВЫХ СООБЩЕНИЙ ---

@router.message(lambda m: m.text and not m.text.startswith('/'))
async def handle_text_message(m: types.Message):
    """Реагирует на обычный текст (например, 'Привет')"""
    kb = get_main_keyboard(m.chat.id)
    await m.answer(
        "❓ <b>Я не понял команду.</b>\nНажми /start или выбери кнопку в меню 👇",
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.message(lambda m: m.text and m.text.startswith('/') and m.text not in ['/start', '/menu'])
async def handle_unknown_command(m: types.Message):
    """Реагирует на неизвестные команды"""
    await m.answer("❓ <b>Такой команды нет.</b>\nИспользуй /start", parse_mode="HTML")