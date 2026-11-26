import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Update

MAX_TRACKED_USERS = 10_000


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self._last_request: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(getattr(event, "event", None), "from_user", None)
        if user is not None:
            now = time.monotonic()
            previous = self._last_request.get(user.id)
            if previous is not None:
                wait = self.delay - (now - previous)
                if wait > 0:
                    await asyncio.sleep(wait)
            if len(self._last_request) >= MAX_TRACKED_USERS:
                self._last_request.clear()
            self._last_request[user.id] = time.monotonic()
        return await handler(event, data)
