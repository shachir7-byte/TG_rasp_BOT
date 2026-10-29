import time
import asyncio
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.last_request = {}

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if user:
            user_id = user.id
            now = time.time()
            
            # Очистка словаря при разрастании
            if len(self.last_request) > 5000:
                self.last_request.clear()

            if user_id in self.last_request:
                elapsed = now - self.last_request[user_id]
                if elapsed < self.delay:
                    await asyncio.sleep(self.delay - elapsed)
            self.last_request[user_id] = time.time()
            
        return await handler(event, data)