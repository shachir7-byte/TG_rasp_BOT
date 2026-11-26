from __future__ import annotations

import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError

from config import ADMIN_IDS, BOT_TOKEN, DATA_DIR, LOG_FILE, PROXY_URL
from database import close_db_connection, init_db
from engines import close_session, health_check, init_session
from handlers import admin, schedule, worker
from middlewares.throttling import ThrottlingMiddleware

logger = logging.getLogger(__name__)
_worker_task: asyncio.Task | None = None


def setup_logging() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("aiohttp_retry").setLevel(logging.WARNING)


setup_logging()

dp = Dispatcher()
dp.include_router(admin.router)
dp.include_router(schedule.router)
dp.update.middleware(ThrottlingMiddleware(delay=0.05))


async def on_startup(bot: Bot) -> None:
    global _worker_task
    logger.info("Запуск бота")
    await init_db()
    await init_session()
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("БД и HTTP-сессия готовы")

    # Health-check движков при старте
    down: list[str] = []
    try:
        health = await health_check()
        for key, ok in health.items():
            if ok:
                logger.info("Движок %s: OK", key)
            else:
                logger.warning("Движок %s: НЕДОСТУПЕН", key)
                down.append(key)
    except Exception as error:
        logger.warning("Health-check при старте не удался: %s", error)

    start_text = "🤖 <b>БОТ ЗАПУЩЕН</b>"
    if down:
        start_text += f"\n⚠️ Недоступны движки: {', '.join(down)}"

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, start_text, parse_mode="HTML")
        except Exception as error:
            logger.warning("Не удалось уведомить админа %s: %s", admin_id, error)

    _worker_task = asyncio.create_task(worker.notification_worker(bot))
    logger.info("Фоновая задача уведомлений запущена")


async def on_shutdown(bot: Bot) -> None:
    global _worker_task
    logger.info("Остановка бота")
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
    await close_session()
    await close_db_connection()
    logger.info("Сессии закрыты")


dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)


async def main() -> None:
    if PROXY_URL:
        logger.info("Используем прокси")
        session = AiohttpSession(proxy=PROXY_URL)
    else:
        session = AiohttpSession()

    bot = Bot(token=BOT_TOKEN, session=session)

    try:
        logger.info("Запуск polling")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    except TelegramConflictError:
        logger.error("Конфликт: бот уже запущен")
    except TelegramNetworkError as error:
        logger.error("Сетевая ошибка: %s", error)
    except Exception as error:
        logger.critical("Критическая ошибка: %s", error)
    finally:
        try:
            await bot.session.close()
            await bot.close()
        except Exception as error:
            logger.error("Ошибка при закрытии бота: %s", error)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
