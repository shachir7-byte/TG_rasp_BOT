import asyncio
import aiohttp
from aiohttp_retry import RetryClient, ExponentialRetry
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, List

from config import GROUPS_CONFIG, CACHE_TTL_SECONDS, LOCAL_TIMEZONE_OFFSET, PROXY_URL

logger = logging.getLogger(__name__)

GLOBAL_SESSION: Optional[RetryClient] = None
SCHEDULE_CACHE: Dict[str, Dict[str, Any]] = {}

def get_tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET)))

async def init_session():
    global GLOBAL_SESSION
    # Увеличенные таймауты: сайт колледжа часто висит под нагрузкой
    timeout = aiohttp.ClientTimeout(total=30.0, connect=15.0)
    retry_options = ExponentialRetry(
        attempts=3,
        start_timeout=1.0,
        max_timeout=15.0,
        factor=1.5,
        exceptions={aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError}
    )
    client_session = aiohttp.ClientSession(timeout=timeout)
    GLOBAL_SESSION = RetryClient(client_session=client_session, retry_options=retry_options)
    logger.info("API Session initialized with retries")

async def close_session():
    global GLOBAL_SESSION
    if GLOBAL_SESSION:
        await GLOBAL_SESSION.close()
        logger.info("API Session closed")

def _calculate_hash(data: list) -> str:
    dump = json.dumps(data, sort_keys=True).encode('utf-8')
    return hashlib.md5(dump).hexdigest()

async def get_week_id(date_str: str) -> Optional[str]:
    """Получение UUID учебной недели с резервированием через PROXY_URL."""
    if not GLOBAL_SESSION:
        logger.error("Session not initialized!")
        return None
        
    url = f"https://sielom.ru/schedule/api/weeks/date/{date_str}"
    
    # 1. Попытка прямого запроса
    try:
        logger.info(f"🔍 Запрос week_id для {date_str} (напрямую)...")
        async with GLOBAL_SESSION.get(url, timeout=7) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and len(data) > 0:
                    w_id = data[0].get('_id') or data[0].get('id')
                    logger.info(f"✅ Найден week_id: {w_id}")
                    return w_id
                elif isinstance(data, dict):
                    w_id = data.get('_id') or data.get('id')
                    logger.info(f"✅ Найден week_id: {w_id}")
                    return w_id
            else:
                logger.warning(f"Прямой запрос вернул HTTP {resp.status}")
    except Exception as e:
        logger.warning(f"Прямой запрос week_id отвалился: {type(e).__name__} - {e}")

    # 2. Резервная попытка через прокси
    if PROXY_URL:
        try:
            logger.info(f"🔄 Резервный запрос week_id через прокси ({PROXY_URL})...")
            async with GLOBAL_SESSION.get(url, proxy=PROXY_URL, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        w_id = data[0].get('_id') or data[0].get('id')
                        logger.info(f"✅ Найден week_id через прокси: {w_id}")
                        return w_id
                    elif isinstance(data, dict):
                        w_id = data.get('_id') or data.get('id')
                        logger.info(f"✅ Найден week_id через прокси: {w_id}")
                        return w_id
                else:
                    logger.warning(f"Прокси-запрос вернул HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Запрос week_id через прокси также упал: {type(e).__name__} - {e}")

    return None

async def fetch_schedule_raw(group_key: str, target_date: Optional[str] = None) -> Optional[List]:
    """Запрос расписания группы с сайта колледжа."""
    if not GLOBAL_SESSION or group_key not in GROUPS_CONFIG:
        return None
    
    group_id = GROUPS_CONFIG[group_key]['id']
    req_date = target_date if target_date else get_tz_now().strftime("%Y-%m-%d")
    
    if not target_date and get_tz_now().weekday() == 6:
        next_mon = get_tz_now() + timedelta(days=(7 - get_tz_now().weekday()))
        req_date = next_mon.strftime("%Y-%m-%d")

    week_id = await get_week_id(req_date)
    if not week_id:
        logger.warning(f"❌ Не удалось определить week_id для {req_date}")
        return None

    url = f"https://sielom.ru/schedule/api/lessons/group/{group_id}"
    params = {"week_id": week_id}

    # 1. Прямой запрос
    try:
        logger.info(f"📥 Запрос пар для группы {group_key} (напрямую)...")
        async with GLOBAL_SESSION.get(url, params=params, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                result = data if isinstance(data, list) else data.get('data', data.get('items', []))
                logger.info(f"📦 Получено {len(result)} записей пар напрямую")
                return result
    except Exception as e:
        logger.warning(f"Прямой запрос уроков не удался: {type(e).__name__} - {e}")

    # 2. Резерв через прокси
    if PROXY_URL:
        try:
            logger.info(f"🔄 Резервный запрос уроков через прокси ({PROXY_URL})...")
            async with GLOBAL_SESSION.get(url, params=params, proxy=PROXY_URL, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data if isinstance(data, list) else data.get('data', data.get('items', []))
                    logger.info(f"📦 Получено {len(result)} записей пар через прокси")
                    return result
        except Exception as e:
            logger.error(f"Запрос уроков через прокси завершился ошибкой: {type(e).__name__} - {e}")

    return None

def parse_lessons(raw_data: list, target_date: Optional[str] = None) -> list:
    """Форматирование ответа: фильтрует день или отдает всю неделю (is_week)."""
    lessons = []
    if not isinstance(raw_data, list):
        return lessons

    for item in raw_data:
        if not isinstance(item, dict):
            continue
            
        if target_date and item.get('date') != target_date:
            continue
        
        subj = item.get('subject', {}) or {}
        teach = item.get('teacher', {}) or {}
        cab = item.get('cabinet', {}) or {}
        
        s_name = subj.get('name') or subj.get('abb_name', 'Нет предмета') if isinstance(subj, dict) else str(subj)
        t_name = teach.get('name') or teach.get('abb_name', 'Нет преп.') if isinstance(teach, dict) else str(teach)
        r_num = cab.get('number', '?') if isinstance(cab, dict) else str(cab) if cab else '?'
        
        t_start = item.get('timeStart', '')
        t_end = item.get('timeEnd', '')
        time_str = f"{t_start}-{t_end}" if t_start and t_end else "??"

        lessons.append({
            "time": time_str,
            "time_start": t_start,
            "time_end": t_end,
            "subject": s_name,
            "teacher": t_name,
            "room": r_num,
            "lesson_number": item.get('lessonNumber', 99),
            "date": item.get('date', ''),
        })
    
    lessons.sort(key=lambda x: x['lesson_number'])
    return lessons

async def get_schedule(group_key: str, target_date: Optional[str] = None, force_refresh: bool = False, is_week: bool = False) -> Tuple[Optional[List], bool]:
    mode = "week" if is_week else "day"
    cache_key = f"{group_key}:{target_date or 'today'}:{mode}"
    now_ts = datetime.now().timestamp()

    if not force_refresh and cache_key in SCHEDULE_CACHE:
        entry = SCHEDULE_CACHE[cache_key]
        if now_ts - entry['timestamp'] < CACHE_TTL_SECONDS:
            logger.info(f"⚡ Кэш HIT ({cache_key}): отдаем {len(entry['data'])} пар")
            return entry['data'], False
        else:
            del SCHEDULE_CACHE[cache_key]

    raw_data = await fetch_schedule_raw(group_key, target_date)
    if raw_data is None:
        logger.error(f"❌ Сбой сети: API не вернуло данные для {cache_key}")
        if cache_key in SCHEDULE_CACHE:
            logger.warning(f"Используем устаревший кэш для {cache_key}")
            return SCHEDULE_CACHE[cache_key]['data'], False
        return None, False

    filter_date = None if is_week else target_date
    parsed_data = parse_lessons(raw_data, filter_date)
    
    new_hash = _calculate_hash(parsed_data)
    changes_detected = False
    
    if cache_key in SCHEDULE_CACHE:
        old_hash = SCHEDULE_CACHE[cache_key].get('hash')
        if old_hash != new_hash:
            changes_detected = True
            logger.info(f"🔔 ОБНАРУЖЕНЫ ИЗМЕНЕНИЯ РАСПИСАНИЯ для {cache_key}!")
    
    SCHEDULE_CACHE[cache_key] = {
        'data': parsed_data,
        'timestamp': now_ts,
        'hash': new_hash
    }
    logger.info(f"💾 Успешно закэшировано {len(parsed_data)} пар для {cache_key}")
    return parsed_data, changes_detected

async def invalidate_cache_for_group(group_key: str):
    keys_to_delete = [k for k in SCHEDULE_CACHE.keys() if k.startswith(f"{group_key}:")]
    for k in keys_to_delete:
        del SCHEDULE_CACHE[k]
    logger.info(f"Кэш сброшен для группы {group_key}")