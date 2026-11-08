from collections import defaultdict
from datetime import datetime, timedelta, timezone
from config import LOCAL_TIMEZONE_OFFSET

LOCAL_TZ = timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET))

NUMBER_EMOJIS = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣",
    5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"
}

DAYS_FULL = [
    'ПОНЕДЕЛЬНИК', 'ВТОРНИК', 'СРЕДА', 'ЧЕТВЕРГ',
    'ПЯТНИЦА', 'СУББОТА', 'ВОСКРЕСЕНЬЕ'
]

# Опциональный словарь для полной расшифровки инициалов преподавателей.
# Если API колледжа отдает сокращенно "Иванов И.И.", а ты хочешь видеть полное имя-отчество,
# просто добавь их сюда по образцу ниже:
TEACHER_FULL_NAMES = {
    # "Иванов И.И.": "Иванов Иван Иванович",
}

def format_teacher_name(raw_teacher: str) -> str:
    """Форматирует ФИО преподавателя и подставляет полное имя из словаря при наличии."""
    cleaned = " ".join(raw_teacher.strip().split())
    if not cleaned or cleaned.lower() in {"нет преп.", "нет", "-", "—"}:
        return "Не указан"
    return TEACHER_FULL_NAMES.get(cleaned, cleaned)

def is_lesson_now(time_start: str, time_end: str, now: datetime) -> bool:
    if not time_start or not time_end:
        return False
    try:
        today_str = now.strftime("%Y-%m-%d")
        start_dt = datetime.strptime(f"{today_str} {time_start}", "%Y-%m-%d %H:%M").replace(tzinfo=now.tzinfo)
        end_dt = datetime.strptime(f"{today_str} {time_end}", "%Y-%m-%d %H:%M").replace(tzinfo=now.tzinfo)
        return start_dt <= now <= end_dt
    except Exception:
        return False

def _format_single_day(lessons: list, now: datetime, is_today: bool = False) -> list:
    lines = []
    sorted_lessons = sorted(lessons, key=lambda x: int(x.get('lesson_number', 0)))
    
    for l in sorted_lessons:
        num = int(l.get('lesson_number', 0))
        num_str = NUMBER_EMOJIS.get(num, f"[{num}]")
        
        active_marker = " 📍 <b>СЕЙЧАС ИДЕТ</b>" if is_today and is_lesson_now(l.get('time_start'), l.get('time_end'), now) else ""
        time_slot = l.get('time', f"{l.get('time_start', '')} - {l.get('time_end', '')}")
        
        lines.append(f"{num_str} <b>{num}-я пара</b> ({time_slot}){active_marker}")
        lines.append(f"📖 <b>{l.get('subject', '—')}</b>")
        
        teacher_raw = l.get('teacher', '').strip()
        if teacher_raw:
            full_teacher = format_teacher_name(teacher_raw)
            lines.append(f"👨‍🏫 Преподаватель: <i>{full_teacher}</i>")
            
        room = l.get('room', '').strip()
        if room:
            lines.append(f"🚪 Кабинет: <code>{room}</code>")
            
        lines.append("──────────────────────")
        
    return lines

def format_schedule_text(title: str, lessons: list, is_week: bool, now: datetime) -> str:
    if not lessons:
        return (
            f"📅 <b>{title}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 <i>Пар нет, можно отдыхать!</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
    
    lines = [f"📅 <b>{title}</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    today_str = now.strftime("%Y-%m-%d")
    
    if is_week:
        by_date = defaultdict(list)
        for l in lessons:
            by_date[l['date']].append(l)
        
        for date_str in sorted(by_date.keys()):
            try:
                d_obj = datetime.strptime(date_str, "%Y-%m-%d")
                day_name = DAYS_FULL[d_obj.weekday()]
                is_curr_day = (date_str == today_str)
                day_badge = " [СЕГОДНЯ]" if is_curr_day else ""
                lines.append(f"\n<b>🗓 {day_name} | {d_obj.strftime('%d.%m.%Y')}{day_badge}</b>")
            except Exception:
                lines.append(f"\n<b>🗓 {date_str}</b>")
            
            lines.append("──────────────────────")
            lines.extend(_format_single_day(by_date[date_str], now, is_today=(date_str == today_str)))
    else:
        is_today = any(l.get('date') == today_str for l in lessons)
        lines.append("")
        lines.extend(_format_single_day(lessons, now, is_today=is_today))
        
    return "\n".join(lines)