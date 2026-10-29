import asyncio
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from config import LOCAL_TIMEZONE_OFFSET
from database import (
    get_active_users_for_worker, update_last_sent, clear_stats
)
from utils.api_client import get_schedule
from utils.formatter import format_schedule_text

logger = logging.getLogger(__name__)
TZ = timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET))

async def notification_worker(bot):
    logger.info("Worker started")
    last_cleared_date = None

    while True:
        try:
            now = datetime.now(TZ)
            cur_time = now.strftime("%H:%M")
            today_str = now.strftime("%Y-%m-%d")
            
            # Очистка статистики один раз в сутки после полуночи
            if cur_time == "00:01" and last_cleared_date != today_str:
                try:
                    await clear_stats()
                    last_cleared_date = today_str
                    logger.info("Stats cleared")
                except Exception as e:
                    logger.error(f"Error clearing stats: {e}")

            active_users = await get_active_users_for_worker()
            if active_users:
                groups_to_check = defaultdict(list)
                for u in active_users:
                    chat_id, n_time, n_type, n_off, last_sent, user_group = u
                    groups_to_check[user_group].append(u)

                for group_key, users in groups_to_check.items():
                    try:
                        lessons, changes_detected = await get_schedule(group_key, today_str, force_refresh=False)
                    except Exception as e:
                        logger.error(f"Failed to fetch schedule for {group_key}: {e}")
                        continue

                    if changes_detected:
                        logger.info(f"Changes detected for group {group_key}")

                    for user in users:
                        chat_id, n_time, n_type, n_off, last_sent, _ = user
                        
                        if last_sent == today_str:
                            continue

                        should_send = False

                        # 1. По точному времени (ЧЧ:ММ)
                        if n_type == 'time' and n_time == cur_time:
                            should_send = True

                        # 2. За N часов до первой пары
                        elif n_type == 'offset' and n_off > 0 and lessons:
                            valid_lessons = [l for l in lessons if l.get('time_start')]
                            if valid_lessons:
                                first_lesson = min(valid_lessons, key=lambda x: x['time_start'])
                                try:
                                    h, m = map(int, first_lesson['time_start'].split(':'))
                                    lesson_minutes = h * 60 + m
                                    notify_minutes = lesson_minutes - (n_off * 60)
                                    
                                    if notify_minutes >= 0:
                                        nh, nm = divmod(notify_minutes, 60)
                                        target_time_str = f"{nh:02d}:{nm:02d}"
                                        if cur_time == target_time_str:
                                            should_send = True
                                except Exception as e:
                                    logger.error(f"Error calculating offset time: {e}")

                        if should_send:
                            try:
                                title = f"🔔 Расписание на {now.strftime('%d.%m')}"
                                text = format_schedule_text(title, lessons, is_week=False, now=now)
                                
                                await bot.send_message(chat_id, text, parse_mode="HTML")
                                await update_last_sent(chat_id, today_str)
                                logger.info(f"Notification sent to {chat_id} ({group_key})")
                                
                                if changes_detected:
                                    await bot.send_message(
                                        chat_id, 
                                        "⚠️ <b>Внимание!</b> В расписании произошли изменения.", 
                                        parse_mode="HTML"
                                    )
                                await asyncio.sleep(0.05)
                            except Exception as e:
                                logger.error(f"Error sending notification to {chat_id}: {e}")

        except Exception as e:
            logger.error(f"Worker loop critical error: {e}")
            await asyncio.sleep(10)
        
        # Спим ровно до начала следующей минуты, чтобы никогда не пропустить триггер по времени
        sleep_seconds = 60 - datetime.now(TZ).second
        await asyncio.sleep(max(1, sleep_seconds))