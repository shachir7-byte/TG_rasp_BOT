import asyncio
import logging
from logging.handlers import RotatingFileHandler
import re
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError

from config import BOT_TOKEN, ADMIN_ID, PROXY_URL
from database import init_db, close_db_connection
from utils.api_client import init_session, close_session
from middlewares.throttling import ThrottlingMiddleware
from handlers import admin, schedule, worker

# =====================================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ И ЗАЩИТЫ ОТ СЕТЕВОГО СПАМА В ЛОГАХ
# =====================================================================
class InstantRotatingFileHandler(RotatingFileHandler):
    """
    Кастомный файловый обработчик: принудительно вызывает flush()
    после каждой записи, устраняя системную задержку буферизации.
    """
    def emit(self, record):
        super().emit(record)
        self.flush()

class TelegramNetworkSpamFilter(logging.Filter):
    """
    Фильтр для логгера aiogram.dispatcher:
    Если на ПК временно упал прокси/VPN, не дает забивать bot.log тысячами
    одинаковых ошибок каждые 5 секунд. Логирует 1-ю попытку и далее каждую 50-ю.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "Failed to fetch updates" in msg:
            # Скрываем дублирующиеся строки ERROR, так как следом всегда идет строка с номером попытки
            return False
        if "Sleep for" in msg and "tryings =" in msg:
            match = re.search(r"tryings\s*=\s*(\d+)", msg)
            if match:
                tries = int(match.group(1))
                # Записываем в лог только начало обрыва (0) и затем каждую 50-ю попытку
                return tries == 0 or tries % 50 == 0
        return True

# Настраиваем корневой (ROOT) логгер для всех модулей проекта
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

# Подключаем фильтр анти-спама к сетевому поллингу aiogram
aiogram_logger = logging.getLogger("aiogram")
aiogram_logger.setLevel(logging.WARNING)
logging.getLogger("aiogram.dispatcher").addFilter(TelegramNetworkSpamFilter())

logger = logging.getLogger(__name__)

# =====================================================================
# ИНИЦИАЛИЗАЦИЯ ДИСПЕТЧЕРА И МИДЛВАРЕЙ
# =====================================================================
dp = Dispatcher()
dp.include_router(admin.router)
dp.include_router(schedule.router)

# Защита от спама кнопками (0.1 сек между кликами)
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

        # Оповещение администратора о старте
        if ADMIN_ID:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    "🤖 <b>БОТ ЗАПУЩЕН</b>\n\nНажмите /start для начала работы.",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление админу: {e}")

        # Запуск фонового воркера напоминаний о занятиях
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
    # Настраиваем укороченный таймаут сессии (25 сек), чтобы прокси не рвал соединение по 60с таймауту
    if PROXY_URL:
        logger.info(f"Используем прокси: {PROXY_URL}")
        session = AiohttpSession(proxy=PROXY_URL, timeout=25.0)
    else:
        logger.info("Работаем напрямую (без прокси)")
        session = AiohttpSession(timeout=25.0)

    bot = Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    try:
        logger.info("Запуск polling...")
        # polling_timeout=15 держит короткие циклы опроса, предотвращая зависание NAT/прокси
        await dp.start_polling(
            bot,
            polling_timeout=15,
            allowed_updates=dp.resolve_used_update_types()
        )
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    except TelegramConflictError:
        logger.error("❌ Конфликт: запущен дубликат процесса бота!")
    except TelegramNetworkError as e:
        logger.error(f"❌ Сетевая ошибка Telegram: {e}")
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        # Закрываем только локальную сессию aiohttp без вызова bot.close()
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