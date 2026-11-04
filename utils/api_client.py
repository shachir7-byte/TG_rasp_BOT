import asyncio
import aiohttp
from aiohttp_retry import RetryClient, ExponentialRetry
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, List

from config import GROUPS_CONFIG, CACHE_TTL_SECONDS, LOCAL_TIMEZONE_OFFSET, PROXY_URL, API_VERSION

logger = logging.getLogger(__name__)

# =====================================================================
# КОНСТАНТЫ ОБЕИХ ВЕРСИЙ API
# =====================================================================
API_V1_URL = "https://sielom.ru/schedule/api"
API_V2_URL = "https://sielom-web.ru/api/schedule"

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
SCHEDULE_CACHE: Dict[str, Dict[str, Any]] = {}

def get_tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET)))

async def init_session():
    global GLOBAL_SESSION
    timeout = aiohttp.ClientTimeout(total=45.0, connect=20.0)
    retry_options = ExponentialRetry(
        attempts=3,
        start_timeout=1.0,
        max_timeout=15.0,
        factor=1.5,
        exceptions={aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError}
    )
    client_session = aiohttp.ClientSession(timeout=timeout, trust_env=True)
    GLOBAL_SESSION = RetryClient(client_session=client_session, retry_options=retry_options)
    logger.info(f"API Session initialized. Selected API_VERSION: {API_VERSION}")

async def close_session():
    global GLOBAL_SESSION
    if GLOBAL_SESSION:
        await GLOBAL_SESSION.close()
        logger.info("API Session closed")

def _calculate_hash(data: list) -> str:
    dump = json.dumps(data, sort_keys=True).encode('utf-8')
    return hashlib.md5(dump).hexdigest()

# =====================================================================
# ДВИЖОК V1 (СТАРЫЙ САЙТ - sielom.ru)
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

def _v1_parse_lessons(raw_data: list, target_date: Optional[str]) -> list:
    lessons = []
    if not isinstance(raw_data, list): return lessons

    for item in raw_data:
        if not isinstance(item, dict): continue
        if target_date and item.get('date') != target_date: continue
        
        subj = item.get('subject', {}) or {}
        teach = item.get('teacher', {}) or {}
        cab = item.get('cabinet', {}) or {}
        
        s_name = subj.get('name') or subj.get('abb_name', 'Нет предмета') if isinstance(subj, dict) else str(subj)
        t_name = teach.get('name') or teach.get('abb_name', 'Нет преп.') if isinstance(teach, dict) else str(teach)
        r_num = cab.get('number', '?') if isinstance(cab, dict) else str(cab) if cab else '?'
        
        t_start, t_end = item.get('timeStart', ''), item.get('timeEnd', '')
        lessons.append({
            "time": f"{t_start}-{t_end}" if t_start and t_end else "??",
            "time_start": t_start, "time_end": t_end,
            "subject": s_name, "teacher": t_name, "room": r_num,
            "lesson_number": item.get('lessonNumber', 99),
            "date": item.get('date', ''),
        })
    lessons.sort(key=lambda x: x['lesson_number'])
    return lessons

# =====================================================================
# ДВИЖОК V2 (НОВЫЙ САЙТ - sielom-web.ru)
# =====================================================================
async def _v2_fetch_schedule(building_code: str, monday_str: str) -> Optional[List]:
    url = f"{API_V2_URL}/{building_code}/"
    params = {"week": monday_str}

    try:
        async with GLOBAL_SESSION.get(url, params=params, timeout=12) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get('lessons', [])
    except Exception as e:
        logger.warning(f"V2: Прямой запрос пар ({building_code}) не удался: {e}")

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

def _v2_parse_lessons(raw_data: list, group_name: str, target_date: Optional[str]) -> list:
    lessons = []
    if not isinstance(raw_data, list): return lessons

    for item in raw_data:
        if not isinstance(item, list) or len(item) < 7: continue
        
        item_group, item_date = str(item[0]).strip(), str(item[1]).strip()
        
        if item_group != group_name: continue
        if target_date and item_date != target_date: continue
            
        lesson_num = int(item[2]) if str(item[2]).isdigit() else 99
        subgroup, subject, cabinet, teacher = item[3], str(item[4]).strip(), str(item[5]).strip(), str(item[6]).strip()
        
        times = LESSON_TIMES.get(lesson_num, {"start": "", "end": ""})
        t_start, t_end = times["start"], times["end"]
        
        if subgroup and str(subgroup) != "0":
            subject = f"[{subgroup} п/г] {subject}"

        lessons.append({
            "time": f"{t_start}-{t_end}" if t_start and t_end else "??",
            "time_start": t_start, "time_end": t_end,
            "subject": subject, "teacher": teacher, "room": cabinet,
            "lesson_number": lesson_num, "date": item_date,
        })
    
    lessons.sort(key=lambda x: x['lesson_number'])
    return lessons

# =====================================================================
# ГЛАВНЫЙ ИНТЕРФЕЙС
# =====================================================================
async def get_schedule(group_key: str, target_date: Optional[str] = None, force_refresh: bool = False, is_week: bool = False) -> Tuple[Optional[List], bool]:
    if not GLOBAL_SESSION or group_key not in GROUPS_CONFIG:
        return None, False

    mode = "week" if is_week else "day"
    cache_key = f"{group_key}:{target_date or 'today'}:{mode}"
    now_ts = datetime.now().timestamp()
    
    # 1. Проверка кэша
    if not force_refresh and cache_key in SCHEDULE_CACHE:
        entry = SCHEDULE_CACHE[cache_key]
        if now_ts - entry['timestamp'] < CACHE_TTL_SECONDS:
            logger.info(f"⚡ Кэш HIT ({cache_key})")
            return entry['data'], False
        else:
            del SCHEDULE_CACHE[cache_key]

    # 2. Вычисление базовых дат
    req_date = target_date if target_date else get_tz_now().strftime("%Y-%m-%d")
    if not target_date and get_tz_now().weekday() == 6:
        next_mon = get_tz_now() + timedelta(days=(7 - get_tz_now().weekday()))
        req_date = next_mon.strftime("%Y-%m-%d")

    filter_date = None if is_week else req_date
    parsed_data = []

    # 3. Маршрутизация запроса в зависимости от версии API
    if API_VERSION == 1:
        group_id = GROUPS_CONFIG[group_key]['id']
        raw_data = await _v1_fetch_schedule(group_id, req_date)
        if raw_data is None:
            return SCHEDULE_CACHE.get(cache_key, {}).get('data'), False
        parsed_data = _v1_parse_lessons(raw_data, filter_date)

    elif API_VERSION == 2:
        group_name = GROUPS_CONFIG[group_key]['name']
        # Простая логика определения корпуса (можно расширить при добавлении новых групп)
        building_code = "b5" if "Д 23" in group_name or "ГД" in group_name else "b7"
        
        # Для V2 нужна дата понедельника
        dt = datetime.strptime(req_date, "%Y-%m-%d")
        monday = dt - timedelta(days=dt.weekday())
        
        raw_data = await _v2_fetch_schedule(building_code, monday.strftime("%Y-%m-%d"))
        if raw_data is None:
            return SCHEDULE_CACHE.get(cache_key, {}).get('data'), False
        parsed_data = _v2_parse_lessons(raw_data, group_name, filter_date)

    else:
        logger.error(f"Неизвестная версия API: {API_VERSION}")
        return None, False
    
    # 4. Проверка хеша и сохранение
    new_hash = _calculate_hash(parsed_data)
    changes_detected = False
    
    if cache_key in SCHEDULE_CACHE and SCHEDULE_CACHE[cache_key].get('hash') != new_hash:
        changes_detected = True
        logger.info(f"🔔 ОБНАРУЖЕНЫ ИЗМЕНЕНИЯ РАСПИСАНИЯ для {cache_key}!")
    
    SCHEDULE_CACHE[cache_key] = {'data': parsed_data, 'timestamp': now_ts, 'hash': new_hash}
    logger.info(f"💾 Закэшировано {len(parsed_data)} пар для {cache_key} (v{API_VERSION})")
    
    return parsed_data, changes_detected

async def invalidate_cache_for_group(group_key: str):
    keys_to_delete = [k for k in SCHEDULE_CACHE.keys() if k.startswith(f"{group_key}:")]
    for k in keys_to_delete:
        del SCHEDULE_CACHE[k]