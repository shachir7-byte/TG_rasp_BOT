import asyncio
import aiohttp
from aiohttp_retry import RetryClient, ExponentialRetry
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, List

from config import GROUPS_CONFIG, CACHE_TTL_SECONDS, LOCAL_TIMEZONE_OFFSET

logger = logging.getLogger(__name__)

GLOBAL_SESSION: Optional[RetryClient] = None
SCHEDULE_CACHE: Dict[str, Dict[str, Any]] = {}

def get_tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=LOCAL_TIMEZONE_OFFSET)))

async def init_session():
    global GLOBAL_SESSION
    retry_options = ExponentialRetry(
        attempts=5,
        start_timeout=0.5,
        max_timeout=30.0,
        exceptions={aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError}
    )
    GLOBAL_SESSION = RetryClient(retry_options=retry_options)
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
    if not GLOBAL_SESSION:
        logger.error("Session not initialized!")
        return None
    url = f"https://sielom.ru/schedule/api/weeks/date/{date_str}"
    try:
        async with GLOBAL_SESSION.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and len(data) > 0:
                    return data[0].get('_id') or data[0].get('id')
                elif isinstance(data, dict):
                    return data.get('_id') or data.get('id')
    except Exception as e:
        logger.error(f"Error getting week_id: {e}")
    return None

async def fetch_schedule_raw(group_key: str, target_date: Optional[str] = None) -> List:
    if not GLOBAL_SESSION:
        logger.error("Session not initialized!")
        return []
        
    if group_key not in GROUPS_CONFIG:
        return []
    
    group_id = GROUPS_CONFIG[group_key]['id']
    req_date = target_date if target_date else get_tz_now().strftime("%Y-%m-%d")
    
    if not target_date and get_tz_now().weekday() == 6:
        next_mon = get_tz_now() + timedelta(days=(7 - get_tz_now().weekday()))
        req_date = next_mon.strftime("%Y-%m-%d")

    week_id = await get_week_id(req_date)
    if not week_id:
        logger.warning(f"Week ID not found for {req_date}")
        return []

    url = f"https://sielom.ru/schedule/api/lessons/group/{group_id}"
    params = {"week_id": week_id}

    try:
        async with GLOBAL_SESSION.get(url, params=params, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return data.get('data', data.get('items', []))
    except Exception as e:
        logger.error(f"API Fetch Error: {e}")
    
    return []

def parse_lessons(raw_data: list, target_date: Optional[str] = None) -> list:
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

async def get_schedule(group_key: str, target_date: Optional[str] = None, force_refresh: bool = False) -> Tuple[List, bool]:
    cache_key = f"{group_key}:{target_date or 'today'}"
    now_ts = datetime.now().timestamp()

    # 1. Проверка кэша
    if not force_refresh and cache_key in SCHEDULE_CACHE:
        entry = SCHEDULE_CACHE[cache_key]
        if now_ts - entry['timestamp'] < CACHE_TTL_SECONDS:
            logger.debug(f"Cache HIT: {cache_key}")
            return entry['data'], False
        else:
            logger.debug(f"Cache EXPIRED: {cache_key}")
            del SCHEDULE_CACHE[cache_key]

    # 2. Запрос к API
    logger.debug(f"Fetching API for: {cache_key}")
    raw_data = await fetch_schedule_raw(group_key, target_date)
    parsed_data = parse_lessons(raw_data, target_date)
    
    # 3. Детектор изменений
    new_hash = _calculate_hash(parsed_data)
    changes_detected = False
    
    if cache_key in SCHEDULE_CACHE:
        old_hash = SCHEDULE_CACHE[cache_key].get('hash')
        if old_hash != new_hash:
            changes_detected = True
            logger.info(f"CHANGES DETECTED for {cache_key}!")
    
    # 4. Обновление кэша
    SCHEDULE_CACHE[cache_key] = {
        'data': parsed_data,
        'timestamp': now_ts,
        'hash': new_hash
    }
    
    return parsed_data, changes_detected

async def invalidate_cache_for_group(group_key: str):
    keys_to_delete = [k for k in SCHEDULE_CACHE.keys() if k.startswith(f"{group_key}:")]
    for k in keys_to_delete:
        del SCHEDULE_CACHE[k]
    logger.info(f"Cache invalidated for group {group_key}") 