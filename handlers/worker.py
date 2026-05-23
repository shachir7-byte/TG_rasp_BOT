import asyncio
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from config import LOCAL_TIMEZONE_OFFSET
from database import (
    get_active_users_for_worker, update_last_sent, get_user_settings
)
from utils.api_client import get_schedule
from utils.formatter import format_schedule_text

logger = logging.getLogger(__name__)
TZ = timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET))

async def notification_worker(bot):
    logger.info("Worker started")
    while True:
        try:
            now = datetime.now(TZ)
            cur_time = now.strftime("%H:%M")
            today_str = now.strftime("%Y-%m-%d")
            
            # Очистка статистики в 00:01
            if cur_time == "00:01":
                from database import clear_stats
                try:
                    await clear_stats()
                    logger.info("Stats cleared")
                except Exception as e:
                    logger.error(f"Error clearing stats: {e}")

            # Получаем всех активных пользователей
            active_users = await get_active_users_for_worker()
            if not active_users:
                await asyncio.sleep(60)
                continue

            # Группируем пользователей по группе, чтобы не дергать API лишний раз
            # Структура active_users: (chat_id, n_time, n_type, n_off, last_sent, user_group)
            groups_to_check = defaultdict(list)
            for u in active_users:
                chat_id, n_time, n_type, n_off, last_sent, user_group = u
                groups_to_check[user_group].append(u)

            for group_key, users in groups_to_check.items():
                # Получаем расписание ОДИН РАЗ для всей группы
                # force_refresh=False позволяет использовать кэш и MD5 проверку
                try:
                    lessons, changes_detected = await get_schedule(group_key, today_str, force_refresh=False)
                except Exception as e:
                    logger.error(f"Failed to fetch schedule for {group_key}: {e}")
                    continue

                # Если расписание изменилось (MD5 хэш не совпал), можно отправить уведомление об изменении
                # (Опционально, сейчас просто логируем)
                if changes_detected:
                    logger.info(f"Changes detected for group {group_key}")

                for user in users:
                    chat_id, n_time, n_type, n_off, last_sent, _ = user
                    
                    # Пропускаем, если уже отправляли сегодня
                    if last_sent == today_str:
                        continue

                    should_send = False

                    # 1. Логика: По времени (например, в 08:00)
                    if n_type == 'time' and n_time == cur_time:
                        should_send = True

                    # 2. Логика: За время до пары (Offset)
                    elif n_type == 'offset' and n_off > 0 and lessons:
                        # Ищем первую пару сегодня
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

                    # Отправка уведомления
                    if should_send:
                        try:
                            title = f"🔔 Расписание на {now.strftime('%d.%m')}"
                            text = format_schedule_text(title, lessons, is_week=False, now=now)
                            
                            await bot.send_message(chat_id, text, parse_mode="HTML")
                            await update_last_sent(chat_id, today_str)
                            logger.info(f"Notification sent to {chat_id} ({group_key})")
                            
                            # Если были изменения в расписании, можно добавить второе сообщение
                            if changes_detected:
                                await bot.send_message(
                                    chat_id, 
                                    "⚠️ <b>Внимание!</b> В расписании произошли изменения.", 
                                    parse_mode="HTML"
                                )
                        except Exception as e:
                            logger.error(f"Error sending notification to {chat_id}: {e}")

        except Exception as e:
            logger.error(f"Worker loop critical error: {e}")
            await asyncio.sleep(10)
        
        # Проверка каждую минуту
        await asyncio.sleep(60)