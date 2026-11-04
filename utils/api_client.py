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
# Кэш хранит расписание ПО НЕДЕЛЯМ (ключ: "group_key:YYYY-MM-DD_понедельника")
SCHEDULE_CACHE: Dict[str, Dict[str, Any]] = {}

def get_tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET)))

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
# ДВИЖОК V2 (НОВЫЙ САЙТ - sielom-web.ru)
# =====================================================================
async def _v2_fetch_schedule(building_code: str, monday_str: str) -> Optional[List]:
    url = f"{API_V2_URL}/{building_code}/"
    params = {"week": monday_str}

    # 1. Прямой запрос (обычно работает мгновенно и без прокси)
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

    # Нормализуем имя группы для надежного сравнения (убираем лишние пробелы и дефисы)
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

# =====================================================================
# ГЛАВНЫЙ ИНТЕРФЕЙС (С УМНЫМ НЕДЕЛЬНЫМ КЭШИРОВАНИЕМ)
# =====================================================================
async def get_schedule(group_key: str, target_date: Optional[str] = None, force_refresh: bool = False, is_week: bool = False) -> Tuple[Optional[List], bool]:
    """
    Кэширует сразу всю учебную неделю по дате понедельника.
    Запросы отдельных дней мгновенно фильтруются из недельного кэша без обращения к сети.
    """
    if not GLOBAL_SESSION or group_key not in GROUPS_CONFIG:
        return None, False

    # 1. Вычисляем опорную дату и понедельник этой недели
    req_date = target_date if target_date else get_tz_now().strftime("%Y-%m-%d")
    if not target_date and get_tz_now().weekday() == 6:
        next_mon = get_tz_now() + timedelta(days=1)
        req_date = next_mon.strftime("%Y-%m-%d")

    dt = datetime.strptime(req_date, "%Y-%m-%d")
    monday_dt = dt - timedelta(days=dt.weekday())
    monday_str = monday_dt.strftime("%Y-%m-%d")

    # Единый ключ кэша для всей недели (и для кнопки "Неделя", и для кнопок "Пн"-"Сб")
    cache_key = f"{group_key}:{monday_str}"
    now_ts = datetime.now().timestamp()
    
    week_lessons = None
    changes_detected = False

    # 2. Проверяем, есть ли свежая неделя в кэше
    if not force_refresh and cache_key in SCHEDULE_CACHE:
        entry = SCHEDULE_CACHE[cache_key]
        if now_ts - entry['timestamp'] < CACHE_TTL_SECONDS:
            logger.debug(f"⚡ Кэш HIT ({cache_key})")
            week_lessons = entry['data']
        else:
            del SCHEDULE_CACHE[cache_key]

    # 3. Если в кэше нет или нажата кнопка "Обновить" — качаем с сайта
    if week_lessons is None:
        if API_VERSION == 1:
            group_id = GROUPS_CONFIG[group_key]['id']
            raw_data = await _v1_fetch_schedule(group_id, monday_str)
            if raw_data is not None:
                week_lessons = _v1_parse_lessons(raw_data)
        elif API_VERSION == 2:
            group_name = GROUPS_CONFIG[group_key]['name']
            building_code = "b5" if "Д 23" in group_name or "ГД" in group_name else "b7"
            raw_data = await _v2_fetch_schedule(building_code, monday_str)
            if raw_data is not None:
                week_lessons = _v2_parse_lessons(raw_data, group_name)
        else:
            logger.error(f"Неизвестная версия API: {API_VERSION}")
            return None, False

        # Если сеть упала, пытаемся отдать старый кэш (если он был)
        if week_lessons is None:
            logger.error(f"❌ Сбой сети: API v{API_VERSION} не вернуло данные для {cache_key}")
            fallback = SCHEDULE_CACHE.get(cache_key, {}).get('data')
            if fallback is not None:
                week_lessons = fallback
            else:
                return None, False
        else:
            # Считаем хеш всей недели и обновляем кэш
            new_hash = _calculate_hash(week_lessons)
            if cache_key in SCHEDULE_CACHE and SCHEDULE_CACHE[cache_key].get('hash') != new_hash:
                changes_detected = True
                logger.info(f"🔔 ОБНАРУЖЕНЫ ИЗМЕНЕНИЯ РАСПИСАНИЯ для {cache_key}!")
            
            SCHEDULE_CACHE[cache_key] = {
                'data': week_lessons,
                'timestamp': now_ts,
                'hash': new_hash
            }
            logger.info(f"💾 Закэширована вся неделя ({len(week_lessons)} пар) для {cache_key} (API v{API_VERSION})")

    # 4. Отдаем либо всю неделю, либо мгновенно вырезаем запрошенный день
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