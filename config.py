import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

LOCAL_TIMEZONE_OFFSET = 5
DEFAULT_GROUP = "is"  # <-- ЭТОГО НЕ ХВАТАЛО РАНЬШЕ
CACHE_TTL_SECONDS = 1800

GROUPS_CONFIG = {
    "is": {"name": "ИС-23/9-3", "id": "f7fab01c-26fa-11ee-9f9a-c08a77b51a65"},
    "d": {"name": "Д-23/9-1", "id": "c7b0c54e-38d4-11ee-9f9a-c08a77b51a65"}
}

if not BOT_TOKEN:
    raise ValueError("❌ Не найден BOT_TOKEN в файле .env!")