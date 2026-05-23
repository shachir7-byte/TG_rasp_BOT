import time
import asyncio
from aiogram import BaseMiddleware
from aiogram.types import Update

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.last_request = {}

    async def __call__(self, handler, event: Update, data: dict):
        user_id = None
        if hasattr(event, 'event') and hasattr(event.event, 'from_user'):
            user_id = event.event.from_user.id
        
        if user_id:
            now = time.time()
            if user_id in self.last_request:
                elapsed = now - self.last_request[user_id]
                if elapsed < self.delay:
                    await asyncio.sleep(self.delay - elapsed)
            self.last_request[user_id] = now
        return await handler(event, data)