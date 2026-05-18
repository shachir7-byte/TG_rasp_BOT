import asyncio
from aiogram import Bot

TOKEN = "8671137490:AAH4Gssdr1OGCojx0_VXzCCTVIw8xlglgg0"

async def main():
    bot = Bot(token=TOKEN)
    print("🔪 Убиваю старые соединения...")
    try:
        # drop_pending_updates=True критически важен — он сбрасывает очередь
        await bot.delete_webhook(drop_pending_updates=True) 
        print("✅ Старые соединения сброшены!")
        
        # Проверка связи
        me = await bot.get_me()
        print(f"✅ Бот жив: @{me.username}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
    