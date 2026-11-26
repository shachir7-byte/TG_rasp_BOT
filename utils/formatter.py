from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from html import escape

from engines.base import Lesson

DAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _esc(value) -> str:
    return escape(str(value if value is not None else ""))


def _time(lesson: Lesson) -> str:
    if lesson.time_start and lesson.time_end:
        return f"{lesson.time_start}–{lesson.time_end}"
    return lesson.time_start or lesson.time_end or ""


def is_lesson_now(lesson: Lesson, now: datetime) -> bool:
    if not lesson.time_start or not lesson.time_end:
        return False
    try:
        local_now = now.astimezone() if now.tzinfo else now
        local_now = local_now.replace(tzinfo=None)
        start = datetime.strptime(f"{local_now:%Y-%m-%d} {lesson.time_start}", "%Y-%m-%d %H:%M")
        end = datetime.strptime(f"{local_now:%Y-%m-%d} {lesson.time_end}", "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    return start <= local_now <= end


def _lesson_block(lesson: Lesson, now: datetime) -> str:
    marker = "📍 " if is_lesson_now(lesson, now) else ""
    lines = [f"{marker}<b>{_time(lesson)}</b>", _esc(lesson.subject)]
    if lesson.subgroup:
        lines.append(f"<i>подгруппа {lesson.subgroup}</i>")
    if lesson.teacher:
        lines.append(f"<i>👤 {_esc(lesson.teacher)}</i>")
    lines.append(f"🚪 Каб: <code>{_esc(lesson.room)}</code>")
    return "\n".join(lines)


def format_schedule(title: str, lessons: list[Lesson], is_week: bool, now: datetime) -> str:
    if not lessons:
        return f"📅 <b>{_esc(title)}</b>\n\n🟢 <i>Пар нет, отдыхай!</i>"

    lines = [f"📅 <b>{_esc(title)}</b>\n", "━━━━━━━━━━━━━━━━━━━━━━"]

    if is_week:
        by_date: dict[str, list[Lesson]] = defaultdict(list)
        for lesson in lessons:
            by_date[lesson.date].append(lesson)
        for date_str in sorted(by_date):
            try:
                day = datetime.strptime(date_str, "%Y-%m-%d")
                header = f"🗓 {DAY_SHORT[day.weekday()]} | {day:%d.%m}"
            except ValueError:
                header = date_str
            lines.append(f"\n<b>{header}</b>")
            lines.append("──────────────────")
            for lesson in sorted(by_date[date_str], key=lambda item: (item.slot, item.subgroup)):
                lines.append(_lesson_block(lesson, now))
                lines.append("")
    else:
        for lesson in sorted(lessons, key=lambda item: (item.slot, item.subgroup)):
            lines.append(_lesson_block(lesson, now))
            lines.append("")

    return "\n".join(lines).rstrip()


def split_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        if current and len(current) + len(block) + 2 > limit:
            parts.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        parts.append(current)
    return parts
