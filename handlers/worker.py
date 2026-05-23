import asyncio
import logging
from datetime import datetime, timedelta, timezone
from config import LOCAL_TIMEZONE_OFFSET
from database import get_active_users_for_worker, update_last_sent, get_user_settings
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
            
            # Очистка статистики в полночь
            if cur_time == "00:01":
                from database import clear_stats
                await clear_stats()
                logger.info("Stats cleared")
                
            active_users = await get_active_users_for_worker()
            for chat_id, n_time, n_type, n_off, last_sent, user_group in active_users:
                if last_sent == today_str:
                    continue
                
                should_send = False
                if n_type == 'time' and cur_time == n_time:
                    should_send = True
                elif n_type == 'offset' and n_off > 0:
                    # Логика расчета времени (упрощенно)
                    # Здесь нужна полная логика из твоего старого кода
                    pass 
                
                if should_send:
                    try:
                        lessons, changes = await get_schedule(user_group, today_str, force_refresh=True)
                        title = f"🔔 Расписание на {now.strftime('%d.%m')}"
                        text = format_schedule_text(title, lessons, False, now)
                        
                        await bot.send_message(chat_id, text, parse_mode="HTML")
                        await update_last_sent(chat_id, today_str)
                        logger.info(f"Notify sent to {chat_id}")
                        
                        if changes:
                            await bot.send_message(chat_id, "⚠️ <b>Изменения!</b>", parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Notify error {chat_id}: {e}")
                        
        except Exception as e:
            logger.error(f"Worker loop error: {e}")
            await asyncio.sleep(10)
        
        await asyncio.sleep(30)