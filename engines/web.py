from __future__ import annotations

from .base import Building, EngineIndex, Lesson, ScheduleEngine, normalize_time
from .session import request_json

BASE_URL = "https://sielom-web.ru/api/schedule/"


def _split_time(value: str) -> tuple[str, str]:
    text = (value or "").strip()
    for separator in ("–", "—"):
        if separator in text:
            start, end = text.split(separator, 1)
            return start.strip(), end.strip()
    if "-" in text:
        start, end = text.split("-", 1)
        return start.strip(), end.strip()
    return text, ""


class WebEngine(ScheduleEngine):
    key = "v2"
    name = "sielom-web.ru"
    description = "Новый API: корпуса Мелик-Карамова, Пролетарский и другие"
    primary = True

    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url.rstrip("/") + "/"

    async def fetch_index(self) -> EngineIndex:
        data = await request_json(self.base_url)
        buildings = []
        for item in data.get("buildings", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            buildings.append(
                Building(
                    id=str(item.get("id", "")),
                    name=str(item.get("name", "")),
                    groups=[str(group) for group in item.get("groups", [])],
                    rooms=[str(room) for room in item.get("rooms", [])],
                )
            )
        return EngineIndex(
            weeks=[str(week) for week in data.get("weeks", [])],
            week=str(data.get("week", "")),
            from_date=str(data.get("from", "")),
            to_date=str(data.get("to", "")),
            generated_at=data.get("generatedAt"),
            buildings=buildings,
            teachers=data.get("teachers", {}),
        )

    async def fetch_lessons(self, building: Building, week: str) -> list[Lesson]:
        data = await request_json(self.base_url + building.id + "/", params={"week": week})
        raw = data.get("lessons", []) if isinstance(data, dict) else data
        lessons: list[Lesson] = []
        for row in raw:
            if not isinstance(row, (list, tuple)) or len(row) < 8:
                continue
            group, date, slot, subgroup, subject, room, teacher, time_value = row[:8]
            start, end = _split_time(time_value)
            lessons.append(
                Lesson(
                    group=str(group),
                    date=str(date),
                    slot=int(slot or 0),
                    subgroup=int(subgroup or 0),
                    subject=str(subject or "Без названия"),
                    room=str(room or "—"),
                    teacher=str(teacher or ""),
                    time_start=normalize_time(start),
                    time_end=normalize_time(end),
                )
            )
        return lessons
