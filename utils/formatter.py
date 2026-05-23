from datetime import datetime, timedelta, timezone
from config import LOCAL_TIMEZONE_OFFSET

LOCAL_TZ = timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET))

def is_lesson_now(time_start: str, time_end: str, now: datetime) -> bool:
    if not time_start or not time_end:
        return False
    try:
        today_str = now.strftime("%Y-%m-%d")
        start_dt = datetime.strptime(f"{today_str} {time_start}", "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(f"{today_str} {time_end}", "%Y-%m-%d %H:%M")
        return start_dt <= now <= end_dt
    except:
        return False

def format_schedule_text(title: str, lessons: list, is_week: bool, now: datetime) -> str:
    if not lessons:
        return f"📅 <b>{title}</b>\n\n🟢 <i>Пар нет, отдыхай!</i>"
    
    lines = [f"📅 <b>{title}</b>\n", "━━━━━━━━━━━━━━━━━━━━━━"]
    
    if is_week:
        from collections import defaultdict
        by_date = defaultdict(list)
        for l in lessons:
            by_date[l['date']].append(l)
        
        for date_str in sorted(by_date.keys()):
            try:
                d_obj = datetime.strptime(date_str, "%Y-%m-%d")
                day_name = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'][d_obj.weekday()]
                lines.append(f"\n<b>🗓 {day_name} | {d_obj.strftime('%d.%m')}</b>")
            except:
                lines.append(f"\n<b>{date_str}</b>")
            
            lines.append("──────────────────────")
            day_lessons = sorted(by_date[date_str], key=lambda x: x['lesson_number'])
            
            for l in day_lessons:
                marker = "📍 " if is_lesson_now(l['time_start'], l['time_end'], now) else ""
                lines.append(f"{marker}<b>{l['time']}</b>")
                lines.append(f"{l['subject']}")
                lines.append(f"<i>👤 {l['teacher']}</i>")
                lines.append(f"🚪 Каб: <code>{l['room']}</code>")
                lines.append("")
    else:
        for l in sorted(lessons, key=lambda x: x['lesson_number']):
            marker = "📍 " if is_lesson_now(l['time_start'], l['time_end'], now) else ""
            lines.append(f"\n{marker}<b>{l['time']}</b>")
            lines.append(f"{l['subject']}")
            lines.append(f"<i>👤 {l['teacher']}</i>")
            lines.append(f"🚪 Каб: <code>{l['room']}</code>")
            
    return "\n".join(lines)