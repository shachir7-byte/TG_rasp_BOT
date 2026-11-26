from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

from config import CACHE_TTL_SECONDS, PRIMARY_ENGINE, TIMEZONE

from .base import Building, EngineIndex, Lesson, ScheduleEngine, ScheduleUnavailable
from .legacy import LegacyEngine
from .session import close_session, init_session
from .web import WebEngine

logger = logging.getLogger(__name__)

ENGINES: dict[str, ScheduleEngine] = {
    "v1": LegacyEngine(),
    "v2": WebEngine(),
}

_index_cache: dict[str, tuple[float, EngineIndex]] = {}
_lessons_cache: dict[str, tuple[float, list[Lesson]]] = {}
_last_responded: dict[str, object] = {"engine": None, "ok": False, "at": None}


def engine_order() -> list[ScheduleEngine]:
    return sorted(ENGINES.values(), key=lambda engine: (not engine.primary, engine.key))


def get_engine(key: str) -> ScheduleEngine:
    try:
        return ENGINES[key]
    except KeyError as error:
        raise ScheduleUnavailable(f"Неизвестный движок: {key}") from error


def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def week_monday(day: date | None = None) -> str:
    day = day or now_local().date()
    return (day - timedelta(days=day.weekday())).isoformat()


def mark_responded(engine_key: str, ok: bool) -> None:
    _last_responded.update(engine=engine_key, ok=ok, at=now_local().isoformat(timespec="seconds"))


def last_responded() -> dict:
    return dict(_last_responded)


async def get_index(engine_key: str, force: bool = False) -> EngineIndex:
    now = time.monotonic()
    cached = _index_cache.get(engine_key)
    if not force and cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    index = await get_engine(engine_key).fetch_index()
    _index_cache[engine_key] = (now, index)
    return index


async def resolve_group(engine_key: str, group: str) -> Building:
    index = await get_index(engine_key)
    for building in index.buildings:
        if group in building.groups:
            return building
    raise ScheduleUnavailable(f"Группа «{group}» не найдена в движке {engine_key}")


async def get_group_schedule(engine_key: str, group: str, week: str | None = None) -> list[Lesson]:
    building = await resolve_group(engine_key, group)
    index = await get_index(engine_key)
    week = week or index.week or week_monday()
    cache_key = f"{engine_key}:{building.id}:{week}"
    now = time.monotonic()
    cached = _lessons_cache.get(cache_key)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        lessons = cached[1]
    else:
        lessons = await get_engine(engine_key).fetch_lessons(building, week)
        _lessons_cache[cache_key] = (now, lessons)
    return [lesson for lesson in lessons if lesson.group == group]


async def fetch_group_schedule(
    group: str, week: str | None = None, engine_key: str | None = None
) -> tuple[list[Lesson], ScheduleEngine]:
    engines = [get_engine(engine_key)] if engine_key else engine_order()
    errors: list[str] = []
    for engine in engines:
        try:
            lessons = await get_group_schedule(engine.key, group, week)
            mark_responded(engine.key, True)
            return lessons, engine
        except ScheduleUnavailable as error:
            mark_responded(engine.key, False)
            errors.append(f"{engine.key}: {error}")
        except Exception as error:
            mark_responded(engine.key, False)
            errors.append(f"{engine.key}: {error}")
    raise ScheduleUnavailable("; ".join(errors) or "нет доступных движков")


async def group_options(engine_key: str | None = None) -> list[dict]:
    index = await get_index(engine_key or PRIMARY_ENGINE)
    options = []
    for building in index.buildings:
        for group in building.groups:
            options.append(
                {
                    "engine": engine_key or PRIMARY_ENGINE,
                    "building_id": building.id,
                    "building": building.name,
                    "group": group,
                }
            )
    return options


async def health_check() -> dict[str, bool]:
    results: dict[str, bool] = {}
    for key in ENGINES:
        try:
            index = await get_index(key, force=True)
            if index.buildings:
                await ENGINES[key].fetch_lessons(index.buildings[0], index.week or week_monday())
            results[key] = True
        except Exception:
            results[key] = False
    return results
