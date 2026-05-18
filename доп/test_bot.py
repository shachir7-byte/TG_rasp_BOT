import asyncio
from aiogram import Bot

async def test():
    token = "8671137490:AAH4Gssdr1OGCojx0_VXzCCTVIw8xlglgg0"  # Тот же, что в main.py
    bot = Bot(token=token)
    
    try:
        me = await bot.me()
        print(f"✅ Токен правильный!")
        print(f"Бот: @{me.username}")
        print(f"Имя: {me.first_name}")
    except Exception as e:
        print(f"❌ Токен НЕВЕРНЫЙ: {e}")

asyncio.run(test())
##-pip install -r requirements.txt