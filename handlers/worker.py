from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from config import TIMEZONE
from database import get_active_users_for_worker, update_last_sent
from engines import fetch_group_schedule, week_monday
from engines.base import Lesson
from utils.formatter import format_schedule

logger = logging.getLogger(__name__)


def seconds_until_next_minute(now: datetime) -> float:
    return max(1.0, 60.0 - now.second - now.microsecond / 1_000_000)


def first_lesson_start(lessons: list[Lesson]) -> str | None:
    starts = [lesson.time_start for lesson in lessons if lesson.time_start]
    return min(starts) if starts else None


def offset_notify_time(first_start: str | None, offset_hours: int) -> str | None:
    if not first_start:
        return None
    try:
        hours, minutes = map(int, first_start.split(":"))
    except (AttributeError, ValueError):
        return None
    total = hours * 60 + minutes - offset_hours * 60
    if total < 0:
        return None
    return f"{total // 60:02d}:{total % 60:02d}"


async def notification_worker(bot) -> None:
    logger.info("Worker запущен")
    while True:
        now = datetime.now(TIMEZONE)
        try:
            current_time = now.strftime("%H:%M")
            today_str = now.strftime("%Y-%m-%d")
            monday = week_monday(now.date())

            active_users = await get_active_users_for_worker()
            by_group: dict[str, list[dict]] = {}
            for user in active_users:
                if user["last_sent_date"] == today_str:
                    continue
                by_group.setdefault(user["user_group"], []).append(user)

            for group, users in by_group.items():
                if not users:
                    continue
                try:
                    lessons, _ = await fetch_group_schedule(group, monday)
                except Exception as error:
                    logger.error("Не удалось получить расписание %s: %s", group, error)
                    continue

                today_lessons = [lesson for lesson in lessons if lesson.date == today_str]
                first_start = first_lesson_start(today_lessons)

                for user in users:
                    should_send = False
                    if user["notify_type"] == "time" and user["notify_time"] == current_time:
                        should_send = True
                    elif user["notify_type"] == "offset" and user["notify_offset"] > 0:
                        target = offset_notify_time(first_start, user["notify_offset"])
                        should_send = target == current_time

                    if not should_send:
                        continue

                    try:
                        title = f"Расписание на {now:%d.%m}"
                        text = format_schedule(title, today_lessons, is_week=False, now=now)
                        await bot.send_message(user["chat_id"], text, parse_mode="HTML")
                        await update_last_sent(user["chat_id"], today_str)
                        logger.info("Уведомление отправлено %s (%s)", user["chat_id"], group)
                    except Exception as error:
                        logger.error("Ошибка отправки уведомления %s: %s", user["chat_id"], error)

        except Exception as error:
            logger.error("Критическая ошибка worker: %s", error)
            await asyncio.sleep(10)

        await asyncio.sleep(seconds_until_next_minute(datetime.now(TIMEZONE)))
