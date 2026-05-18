import asyncio
from aiogram import Bot

BOT_TOKEN = "8671137490:AAH4Gssdr1OGCojx0_VXzCCTVIw8xlglgg0"

async def main():
    bot = Bot(token=BOT_TOKEN)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("✅ Webhook удален, старые обновления сброшены.")
        
        # Получаем информацию о боте, чтобы проверить связь
        me = await bot.get_me()
        print(f"✅ Связь с сервером есть. Бот: @{me.username}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())