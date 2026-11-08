import asyncio
import aiohttp
from aiohttp_retry import RetryClient, ExponentialRetry
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, List

from config import GROUPS_CONFIG, CACHE_TTL_SECONDS, LOCAL_TIMEZONE_OFFSET, PROXY_URL, API_VERSION

logger = logging.getLogger(__name__)

# =====================================================================
# КОНСТАНТЫ И СОСТОЯНИЕ СИСТЕМЫ API (ОСНОВНОЙ + РЕЗЕРВНЫЙ)
# =====================================================================
API_V1_URL = "https://sielom.ru/schedule/api"
API_V2_URL = "https://sielom-web.ru/api/schedule"

# Текущий приоритетный движок (можно переключать из админки в ТГ на лету)
CURRENT_API_VERSION: int = API_VERSION

# Информация о том, какой именно сервер реально отдал данные последним
LAST_USED_SOURCE: str = "Ожидание первого запроса"
LAST_FALLBACK_USED: bool = False
LAST_ERROR_REASON: str = "Ошибок не зафиксировано"

# Сетка звонков (для V2, так как новый сайт не отдает время пар)
LESSON_TIMES = {
    1: {"start": "08:30", "end": "09:50"},
    2: {"start": "10:00", "end": "11:20"},
    3: {"start": "11:40", "end": "13:00"},
    4: {"start": "13:40", "end": "15:00"},
    5: {"start": "15:20", "end": "16:40"},
    6: {"start": "17:00", "end": "18:20"},
    7: {"start": "18:30", "end": "19:50"},
}

GLOBAL_SESSION: Optional[RetryClient] = None
# Кэш хранит расписание ПО НЕДЕЛЯМ (ключ: "group_key:YYYY-MM-DD_понедельника")
SCHEDULE_CACHE: Dict[str, Dict[str, Any]] = {}

def get_tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET)))

def set_active_api_version(version: int):
    """Переключает основной API (1 или 2) прямо из админ-панели и очищает кэш."""
    global CURRENT_API_VERSION
    CURRENT_API_VERSION = 1 if version == 1 else 2
    SCHEDULE_CACHE.clear()
    logger.info(f"🔄 Админ переключил приоритетный API на версию v{CURRENT_API_VERSION}")

def get_api_status_info() -> Dict[str, Any]:
    """Возвращает детальную сводку по работе основного и резервного API для админки."""
    primary_name = "v2 (sielom-web.ru — Основной)" if CURRENT_API_VERSION == 2 else "v1 (sielom.ru — Резервный)"
    return {
        "configured_version": CURRENT_API_VERSION,
        "configured_name": primary_name,
        "last_source": LAST_USED_SOURCE,
        "is_fallback": LAST_FALLBACK_USED,
        "last_error": LAST_ERROR_REASON,
        "cache_weeks_count": len(SCHEDULE_CACHE)
    }

async def check_both_apis_health() -> Dict[str, Dict[str, Any]]:
    """Пингует оба сервера (основной v2 и резервный v1) для диагностики в админ-панели."""
    results = {
        "v2": {"name": "sielom-web.ru (Основной v2)", "ok": False, "ms": 0, "error": ""},
        "v1": {"name": "sielom.ru (Резервный v1)", "ok": False, "ms": 0, "error": ""}
    }
    if not GLOBAL_SESSION:
        return results

    monday_dt = get_tz_now() - timedelta(days=get_tz_now().weekday())
    monday_str = monday_dt.strftime("%Y-%m-%d")

    # 1. Тест нового API v2 (sielom-web.ru)
    t0 = time.time()
    try:
        async with GLOBAL_SESSION.get(f"{API_V2_URL}/b7/", params={"week": monday_str}, timeout=6) as resp:
            results["v2"]["ms"] = int((time.time() - t0) * 1000)
            if resp.status == 200:
                results["v2"]["ok"] = True
            else:
                results["v2"]["error"] = f"HTTP {resp.status}"
    except Exception as e:
        results["v2"]["ms"] = int((time.time() - t0) * 1000)
        results["v2"]["error"] = type(e).__name__

    # 2. Тест старого API v1 (sielom.ru)
    t1 = time.time()
    try:
        async with GLOBAL_SESSION.get(f"{API_V1_URL}/weeks/date/{monday_str}", timeout=6) as resp:
            results["v1"]["ms"] = int((time.time() - t1) * 1000)
            if resp.status == 200:
                results["v1"]["ok"] = True
            else:
                results["v1"]["error"] = f"HTTP {resp.status}"
    except Exception as e:
        results["v1"]["ms"] = int((time.time() - t1) * 1000)
        results["v1"]["error"] = type(e).__name__

    return results

async def init_session():
    global GLOBAL_SESSION
    timeout = aiohttp.ClientTimeout(total=40.0, connect=15.0)
    retry_options = ExponentialRetry(
        attempts=3,
        start_timeout=1.0,
        max_timeout=12.0,
        factor=1.5,
        exceptions={aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError}
    )
    client_session = aiohttp.ClientSession(timeout=timeout, trust_env=True)
    GLOBAL_SESSION = RetryClient(client_session=client_session, retry_options=retry_options)
    logger.info(f"API Session initialized. Priority API_VERSION: {CURRENT_API_VERSION}")

async def close_session():
    global GLOBAL_SESSION
    if GLOBAL_SESSION:
        await GLOBAL_SESSION.close()
        logger.info("API Session closed")

def _calculate_hash(data: list) -> str:
    dump = json.dumps(data, sort_keys=True).encode('utf-8')
    return hashlib.md5(dump).hexdigest()

# =====================================================================
# ДВИЖОК V1 (РЕЗЕРВНЫЙ САЙТ - sielom.ru)
# =====================================================================
async def _v1_get_week_id(date_str: str) -> Optional[str]:
    url = f"{API_V1_URL}/weeks/date/{date_str}"
    
    try:
        async with GLOBAL_SESSION.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and len(data) > 0:
                    return data[0].get('_id') or data[0].get('id')
                elif isinstance(data, dict):
                    return data.get('_id') or data.get('id')
    except Exception as e:
        logger.warning(f"V1: Прямой запрос week_id отвалился: {e}")

    if PROXY_URL:
        try:
            proxy_conn = aiohttp.TCPConnector(ssl=False, enable_cleanup_closed=True)
            async with aiohttp.ClientSession(connector=proxy_conn, trust_env=True) as proxy_sess:
                async with proxy_sess.get(url, proxy=PROXY_URL, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            return data[0].get('_id') or data[0].get('id')
                        elif isinstance(data, dict):
                            return data.get('_id') or data.get('id')
        except Exception as e:
            logger.error(f"V1: Запрос week_id через прокси упал: {e}")

    return None

async def _v1_fetch_schedule(group_id: str, req_date: str) -> Optional[List]:
    week_id = await _v1_get_week_id(req_date)
    if not week_id:
        return None

    url = f"{API_V1_URL}/lessons/group/{group_id}"
    params = {"week_id": week_id}

    try:
        async with GLOBAL_SESSION.get(url, params=params, timeout=12) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data if isinstance(data, list) else data.get('data', data.get('items', []))
    except Exception as e:
        logger.warning(f"V1: Прямой запрос уроков не удался: {e}")

    if PROXY_URL:
        try:
            proxy_conn = aiohttp.TCPConnector(ssl=False, enable_cleanup_closed=True)
            async with aiohttp.ClientSession(connector=proxy_conn, trust_env=True) as proxy_sess:
                async with proxy_sess.get(url, params=params, proxy=PROXY_URL, timeout=20) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data if isinstance(data, list) else data.get('data', data.get('items', []))
        except Exception as e:
            logger.error(f"V1: Запрос уроков через прокси ошибка: {e}")

    return None

def _v1_parse_lessons(raw_data: list) -> list:
    lessons = []
    if not isinstance(raw_data, list):
        return lessons

    for item in raw_data:
        if not isinstance(item, dict):
            continue
        
        subj = item.get('subject', {}) or {}
        teach = item.get('teacher', {}) or {}
        cab = item.get('cabinet', {}) or {}
        
        s_name = subj.get('name') or subj.get('abb_name', 'Нет предмета') if isinstance(subj, dict) else str(subj)
        t_name = teach.get('name') or teach.get('abb_name', 'Нет преп.') if isinstance(teach, dict) else str(teach)
        r_num = cab.get('number', '?') if isinstance(cab, dict) else str(cab) if cab else '?'
        
        t_start, t_end = item.get('timeStart', ''), item.get('timeEnd', '')
        lessons.append({
            "time": f"{t_start}-{t_end}" if t_start and t_end else "??",
            "time_start": t_start,
            "time_end": t_end,
            "subject": s_name,
            "teacher": t_name,
            "room": r_num,
            "lesson_number": item.get('lessonNumber', 99),
            "date": item.get('date', ''),
        })
    lessons.sort(key=lambda x: (x['date'], x['lesson_number']))
    return lessons

# =====================================================================
# ДВИЖОК V2 (ОСНОВНОЙ САЙТ - sielom-web.ru)
# =====================================================================
async def _v2_fetch_schedule(building_code: str, monday_str: str) -> Optional[List]:
    url = f"{API_V2_URL}/{building_code}/"
    params = {"week": monday_str}

    # 1. Прямой запрос
    try:
        async with GLOBAL_SESSION.get(url, params=params, timeout=12) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get('lessons', [])
    except Exception as e:
        logger.warning(f"V2: Прямой запрос пар ({building_code}) не удался: {e}")

    # 2. Резервный запрос через прокси
    if PROXY_URL:
        try:
            proxy_conn = aiohttp.TCPConnector(ssl=False, enable_cleanup_closed=True)
            async with aiohttp.ClientSession(connector=proxy_conn, trust_env=True) as proxy_sess:
                async with proxy_sess.get(url, params=params, proxy=PROXY_URL, timeout=20) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('lessons', [])
        except Exception as e:
            logger.error(f"V2: Запрос пар через прокси ошибка: {e}")

    return None

def _v2_parse_lessons(raw_data: list, group_name: str) -> list:
    lessons = []
    if not isinstance(raw_data, list):
        return lessons

    norm_target_group = group_name.replace("-", " ").replace("  ", " ").strip().upper()

    for item in raw_data:
        if not isinstance(item, list) or len(item) < 7:
            continue
        
        item_group_raw = str(item[0]).replace("-", " ").replace("  ", " ").strip().upper()
        item_date = str(item[1]).strip()
        
        if item_group_raw != norm_target_group:
            continue
            
        lesson_num = int(item[2]) if str(item[2]).isdigit() else 99
        subgroup = item[3]
        subject = str(item[4]).strip()
        cabinet = str(item[5]).strip()
        teacher = str(item[6]).strip()
        
        times = LESSON_TIMES.get(lesson_num, {"start": "", "end": ""})
        t_start, t_end = times["start"], times["end"]
        
        if subgroup and str(subgroup) != "0":
            subject = f"[{subgroup} п/г] {subject}"

        lessons.append({
            "time": f"{t_start}-{t_end}" if t_start and t_end else "??",
            "time_start": t_start,
            "time_end": t_end,
            "subject": subject,
            "teacher": teacher,
            "room": cabinet,
            "lesson_number": lesson_num,
            "date": item_date,
        })
    
    lessons.sort(key=lambda x: (x['date'], x['lesson_number']))
    return lessons

async def _try_fetch_by_version(version: int, group_key: str, monday_str: str) -> Optional[List]:
    """Вспомогательная функция загрузки расписания с конкретного движка (1 или 2)."""
    if version == 2:
        group_name = GROUPS_CONFIG[group_key]['name']
        building_code = "b5" if "Д 23" in group_name or "ГД" in group_name else "b7"
        raw_data = await _v2_fetch_schedule(building_code, monday_str)
        if raw_data is not None:
            return _v2_parse_lessons(raw_data, group_name)
    else:
        group_id = GROUPS_CONFIG[group_key]['id']
        raw_data = await _v1_fetch_schedule(group_id, monday_str)
        if raw_data is not None:
            return _v1_parse_lessons(raw_data)
    return None

# =====================================================================
# ГЛАВНЫЙ ИНТЕРФЕЙС (С АВТО-ФОЛБЭКОМ И НЕДЕЛЬНЫМ КЭШИРОВАНИЕМ)
# =====================================================================
async def get_schedule(group_key: str, target_date: Optional[str] = None, force_refresh: bool = False, is_week: bool = False) -> Tuple[Optional[List], bool]:
    global LAST_USED_SOURCE, LAST_FALLBACK_USED, LAST_ERROR_REASON

    if not GLOBAL_SESSION or group_key not in GROUPS_CONFIG:
        return None, False

    req_date = target_date if target_date else get_tz_now().strftime("%Y-%m-%d")
    if not target_date and get_tz_now().weekday() == 6:
        next_mon = get_tz_now() + timedelta(days=1)
        req_date = next_mon.strftime("%Y-%m-%d")

    dt = datetime.strptime(req_date, "%Y-%m-%d")
    monday_dt = dt - timedelta(days=dt.weekday())
    monday_str = monday_dt.strftime("%Y-%m-%d")

    cache_key = f"{group_key}:{monday_str}"
    now_ts = datetime.now().timestamp()
    
    week_lessons = None
    changes_detected = False

    # 1. Проверка кэша
    if not force_refresh and cache_key in SCHEDULE_CACHE:
        entry = SCHEDULE_CACHE[cache_key]
        if now_ts - entry['timestamp'] < CACHE_TTL_SECONDS:
            logger.debug(f"⚡ Кэш HIT ({cache_key})")
            week_lessons = entry['data']
        else:
            del SCHEDULE_CACHE[cache_key]

    # 2. Запрос к приоритетному API (а если он лежит — автоматический переход на резервный API)
    if week_lessons is None:
        primary_ver = CURRENT_API_VERSION
        backup_ver = 1 if primary_ver == 2 else 2

        week_lessons = await _try_fetch_by_version(primary_ver, group_key, monday_str)
        if week_lessons is not None:
            LAST_USED_SOURCE = f"🟢 Основной v{primary_ver} ({'sielom-web.ru' if primary_ver == 2 else 'sielom.ru'})"
            LAST_FALLBACK_USED = False
        else:
            LAST_ERROR_REASON = f"{datetime.now().strftime('%H:%M:%S')} — Основной API v{primary_ver} не ответил"
            logger.warning(f"⚠️ Основной API v{primary_ver} недоступен! Пробую резервный API v{backup_ver}...")
            week_lessons = await _try_fetch_by_version(backup_ver, group_key, monday_str)
            if week_lessons is not None:
                LAST_USED_SOURCE = f"🟠 АВТО-РЕЗЕРВ v{backup_ver} ({'sielom-web.ru' if backup_ver == 2 else 'sielom.ru'})"
                LAST_FALLBACK_USED = True

        # 3. Если оба сайта лежат — отдаем старый кэш (если сохранился)
        if week_lessons is None:
            LAST_ERROR_REASON = f"{datetime.now().strftime('%H:%M:%S')} — Оба API (v1 и v2) недоступны"
            logger.error(f"❌ Сбой сети: Оба API не вернули данные для {cache_key}")
            fallback = SCHEDULE_CACHE.get(cache_key, {}).get('data')
            if fallback is not None:
                LAST_USED_SOURCE = "🟡 Старый кэш (Оба сайта недоступны)"
                week_lessons = fallback
            else:
                return None, False
        else:
            new_hash = _calculate_hash(week_lessons)
            if cache_key in SCHEDULE_CACHE and SCHEDULE_CACHE[cache_key].get('hash') != new_hash:
                changes_detected = True
                logger.info(f"🔔 ОБНАРУЖЕНЫ ИЗМЕНЕНИЯ РАСПИСАНИЯ для {cache_key}!")
            
            SCHEDULE_CACHE[cache_key] = {
                'data': week_lessons,
                'timestamp': now_ts,
                'hash': new_hash
            }
            logger.info(f"💾 Закэширована неделя ({len(week_lessons)} пар) для {cache_key} | Источник: {LAST_USED_SOURCE}")

    if is_week:
        return week_lessons, changes_detected
    else:
        day_lessons = [l for l in week_lessons if l.get('date') == req_date]
        return day_lessons, changes_detected

async def invalidate_cache_for_group(group_key: str):
    keys_to_delete = [k for k in SCHEDULE_CACHE.keys() if k.startswith(f"{group_key}:")]
    for k in keys_to_delete:
        del SCHEDULE_CACHE[k]
    logger.info(f"Кэш сброшен для группы {group_key}")