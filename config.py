import os
import time
from dotenv import load_dotenv

load_dotenv()

# Время старта бота для расчета Uptime в админ-панели
START_TIME = time.time()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0
PROXY_URL = os.getenv("PROXY_URL")

LOCAL_TIMEZONE_OFFSET = 5
DEFAULT_GROUP = "is"
CACHE_TTL_SECONDS = 1800

# ==========================================
# ТУМБЛЕР ВЕРСИИ API
# 1 = Старый сайт (sielom.ru) - Требует week_id
# 2 = Новый сайт (sielom-web.ru) - Прямой запрос
# ==========================================
API_VERSION = 2

# Конфигурация групп
# ВАЖНО: 'name' должно строго совпадать с тем, что отдает sielom-web.ru (без тире)
GROUPS_CONFIG = {
    "is": {"name": "ИС 23/9-3", "id": "f7fab01c-26fa-11ee-9f9a-c08a77b51a65"},
    "d": {"name": "Д 23/9-1", "id": "c7b0c54e-38d4-11ee-9f9a-c08a77b51a65"}
}

if not BOT_TOKEN:
    raise ValueError("❌ Не найден BOT_TOKEN в файле .env!")