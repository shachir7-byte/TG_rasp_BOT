import os
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {
    int(item) for item in (os.getenv("ADMIN_ID") or "").split(",") if item.strip().isdigit()
}
PROXY_URL = os.getenv("PROXY_URL") or None

TZ_NAME = os.getenv("TIMEZONE", "Asia/Yekaterinburg")
try:
    TIMEZONE = ZoneInfo(TZ_NAME)
except ZoneInfoNotFoundError:
    TIMEZONE = timezone(timedelta(hours=5))

DATA_DIR = os.getenv("DATA_DIR", "data")
DB_FILE = os.path.join(DATA_DIR, "schedule.db")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "1800"))
PRIMARY_ENGINE = os.getenv("PRIMARY_ENGINE", "v2")

if not BOT_TOKEN:
    raise ValueError("Не найден BOT_TOKEN в файле .env")
