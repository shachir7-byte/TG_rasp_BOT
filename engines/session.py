from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .base import ScheduleUnavailable

logger = logging.getLogger(__name__)

_session: aiohttp.ClientSession | None = None

RETRY_ATTEMPTS = 3
RETRY_DELAY = 1.0


def set_session(session: aiohttp.ClientSession | None) -> None:
    global _session
    _session = session


async def init_session() -> None:
    global _session
    if _session is None:
        timeout = aiohttp.ClientTimeout(total=15)
        _session = aiohttp.ClientSession(timeout=timeout)
    logger.info("HTTP-сессия инициализирована")


async def close_session() -> None:
    global _session
    if _session is not None:
        await _session.close()
        _session = None
        logger.info("HTTP-сессия закрыта")


async def request_json(url: str, params: dict[str, str] | None = None) -> Any:
    if _session is None:
        raise ScheduleUnavailable("HTTP-сессия не инициализирована")

    last_error: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            async with _session.get(url, params=params) as response:
                if response.status != 200:
                    raise ScheduleUnavailable(f"HTTP {response.status} для {url}")
                return await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            last_error = error
            logger.warning("Попытка %d/%d для %s: %s", attempt + 1, RETRY_ATTEMPTS, url, error)
            if attempt < RETRY_ATTEMPTS - 1:
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))

    raise ScheduleUnavailable(f"Не удалось получить {url}: {last_error}") from last_error
