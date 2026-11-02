import asyncio
import logging
from logging.handlers import RotatingFileHandler
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError

from config import BOT_TOKEN, ADMIN_ID, PROXY_URL
from database import init_db, close_db_connection, get_all_users, get_user_settings
from utils.api_client import init_session, close_session
from middlewares.throttling import ThrottlingMiddleware
from handlers import admin, schedule, worker

# =====================================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ (МГНОВЕННЫЙ СБРОС БУФЕРА ДЛЯ TAIL -F)
# =====================================================================
class InstantRotatingFileHandler(RotatingFileHandler):
    """
    Кастомный файловый обработчик: принудительно вызывает flush()
    после каждой записи, устраняя системную задержку буферизации.
    """
    def emit(self, record):
        super().emit(record)
        self.flush()

# Настраиваем корневой (ROOT) логгер, чтобы логи собирались со ВСЕХ файлов проекта
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

file_handler = InstantRotatingFileHandler("bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(file_formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_formatter = logging.Formatter("✅ %(levelname)s: %(message)s")
console_handler.setFormatter(console_formatter)

root_logger.handlers.clear()
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# Снижаем шум сетевой библиотеки aiogram, чтобы не засорять логи
logging.getLogger("aiogram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# =====================================================================
# ИНИЦИАЛИЗАЦИЯ ДИСПЕТЧЕРА И МИДЛВАРЕЙ
# =====================================================================
dp = Dispatcher()
dp.include_router(admin.router)
dp.include_router(schedule.router)

# Защита от спама кнопками: аргумент строго delay=0.1
dp.callback_query.middleware(ThrottlingMiddleware(delay=0.1))
dp.message.middleware(ThrottlingMiddleware(delay=0.1))

# =====================================================================
# СОБЫТИЯ ЖИЗНЕННОГО ЦИКЛА БОТА
# =====================================================================
async def on_startup(bot: Bot):
    logger.info("--- ЗАПУСК БОТА (PRO VERSION) ---")
    try:
        await init_db()
        await init_session()
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook сброшен, БД и сессии готовы")

        # Оповещение администратора о старте без технических деталей о файлах логов
        if ADMIN_ID:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    "🤖 <b>БОТ ЗАПУЩЕН</b>\n\nНажмите /start для начала работы.",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление админу: {e}")

        # Фоновый воркер напоминаний о занятиях
        if hasattr(worker, 'notification_worker'):
            asyncio.create_task(worker.notification_worker(bot))
            logger.info("Задача worker запущена")
        elif hasattr(worker, 'start_worker'):
            asyncio.create_task(worker.start_worker(bot))
            logger.info("Задача worker запущена")

    except Exception as e:
        logger.critical(f"Ошибка при старте: {e}", exc_info=True)
        raise

async def on_shutdown(bot: Bot):
    logger.info("--- ОСТАНОВКА БОТА ---")
    try:
        await close_session()
        await close_db_connection()
    except Exception as e:
        logger.error(f"Ошибка закрытия внутренних сессий: {e}")
    logger.info("Сессии закрыты штатно")

dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)

# =====================================================================
# ТОЧКА ВХОДА (MAIN)
# =====================================================================
async def main():
    session = None
    if PROXY_URL:
        logger.info(f"Используем прокси: {PROXY_URL}")
        session = AiohttpSession(proxy=PROXY_URL)
    else:
        session = AiohttpSession()

    bot = Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    try:
        logger.info("Запуск polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    except TelegramConflictError:
        logger.error("❌ Конфликт: запущен дубликат процесса бота!")
    except TelegramNetworkError as e:
        logger.error(f"❌ Сетевая ошибка Telegram: {e}")
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        # ВАЖНО: Закрываем только локальную сессию aiohttp.
        # Метод bot.close() НЕ вызываем, чтобы Telegram API не блокировал бота на 500+ секунд.
        try:
            if bot.session:
                await bot.session.close()
            logger.info("Сессия закрыта")
        except Exception as e:
            logger.error(f"Ошибка при закрытии сессии: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass