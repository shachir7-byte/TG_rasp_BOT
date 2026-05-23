import asyncio
import logging
from logging.handlers import RotatingFileHandler
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError
from config import BOT_TOKEN, ADMIN_ID
from database import init_db, close_db_connection, get_all_users, get_user_settings
from utils.api_client import init_session, close_session
from middlewares.throttling import ThrottlingMiddleware
from handlers import schedule, admin, worker

# --- НАСТРОЙКА ЛОГИРОВАНИЯ ---
logger = logging.getLogger(__name__)

# Создаем файл-логгер (пишет ВСЁ: от DEBUG до CRITICAL)
file_handler = RotatingFileHandler("bot.log", maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(file_formatter)

# Создаем консоль-логгер (пишет только ВАЖНОЕ: WARNING и выше + INFO для наших сообщений)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING) # Скрываем INFO и DEBUG в консоли
console_formatter = logging.Formatter("✅ %(levelname)s: %(message)s")
console_handler.setFormatter(console_formatter)

# Настраиваем наш основной логгер
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# --- ВАЖНО: Убираем спам от aiogram в консоль ---
# Заставляем библиотеку aiogram писать в файл, но молчать в консоль
logging.getLogger("aiogram").setLevel(logging.WARNING) 
logging.getLogger("aiogram").addHandler(file_handler)
# Если очень хочется видеть старт поллинга в консоли, можно добавить отдельный handler, 
# но сейчас мы оставим консоль чистой для ошибок.

dp = Dispatcher()
dp.include_router(schedule.router)
dp.include_router(admin.router)
dp.update.middleware(ThrottlingMiddleware(delay=0.05))

async def on_startup(bot: Bot):
    logger.info("--- ЗАПУСК БОТА (PRO VERSION) ---")
    try:
        await init_db()
        await init_session()
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook сброшен, БД и сессии готовы")
        
        # Уведомление админа
        try:
            await bot.send_message(ADMIN_ID, f"🤖 <b>БОТ ЗАПУЩЕН</b>\nЛоги пишутся в bot.log", parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не удалось уведомить админа: {e}")
        
        # Уведомление пользователей
        users = await get_all_users()
        count = 0
        for u in users:
            chat_id = u[0]
            if chat_id == ADMIN_ID: continue
            try:
                settings = await get_user_settings(chat_id)
                if settings and len(settings) > 6 and settings[6] == 1: # notify_restart индекс 6
                    await bot.send_message(chat_id, "✅ <b>Бот обновлен</b>.", parse_mode="HTML")
                    count += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)
        
        if count > 0:
            logger.info(f"Restart notified to {count} users")
        
        asyncio.create_task(worker.notification_worker(bot))
        logger.info("Worker task created")
        
    except Exception as e:
        logger.critical(f"Ошибка при старте: {e}")
        raise

async def on_shutdown(bot: Bot):
    logger.info("--- ОСТАНОВКА БОТА ---")
    try:
        await close_session()
        await close_db_connection()
    except Exception as e:
        logger.error(f"Ошибка закрытия сессий: {e}")
    logger.info("Sessions closed gracefully")

async def main():
    session = AiohttpSession()
    bot = Bot(token=BOT_TOKEN, session=session)
    
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    try:
        logger.info("Запуск polling...")
        # Выведем факт старта в консоль явно, так как aiogram мы заглушили
        print("🚀 Бот запущен! (Логи в bot.log)")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    except TelegramConflictError:
        logger.error("❌ КОНФЛИКТ: Бот уже запущен!")
    except TelegramNetworkError as e:
        logger.error(f"❌ СЕТЬ: {e}")
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")
    finally:
        try:
            await bot.close()
        except Exception as e:
            logger.error(f"Ошибка при закрытии бота: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass