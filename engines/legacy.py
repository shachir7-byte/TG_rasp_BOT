from __future__ import annotations

from .base import Building, EngineIndex, Lesson, ScheduleEngine, normalize_time
from .session import request_json

BASE_URL = "https://sielom.ru/schedule/api"

LEGACY_GROUPS = {
    "ИС-23/9-3": "f7fab01c-26fa-11ee-9f9a-c08a77b51a65",
    "Д-23/9-1": "c7b0c54e-38d4-11ee-9f9a-c08a77b51a65",
}


def _text(value, fallback: str) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("abb_name") or fallback)
    return str(value) if value else fallback


class LegacyEngine(ScheduleEngine):
    key = "v1"
    name = "sielom.ru"
    description = "Старый API (работал до переезда сайта)"
    primary = False

    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    async def fetch_index(self) -> EngineIndex:
        groups = list(LEGACY_GROUPS.keys())
        building = Building(id="legacy", name="Основной корпус", groups=groups)
        return EngineIndex(
            weeks=[],
            week="",
            from_date="",
            to_date="",
            buildings=[building],
            teachers={},
        )

    async def _week_id(self, date_str: str) -> str:
        data = await request_json(f"{self.base_url}/weeks/date/{date_str}")
        if isinstance(data, list) and data:
            return str(data[0].get("_id") or data[0].get("id") or "")
        if isinstance(data, dict):
            return str(data.get("_id") or data.get("id") or "")
        return ""

    async def fetch_lessons(self, building: Building, week: str) -> list[Lesson]:
        week_id = await self._week_id(week)
        if not week_id:
            return []
        lessons: list[Lesson] = []
        for group in building.groups:
            group_id = LEGACY_GROUPS.get(group)
            if not group_id:
                continue
            data = await request_json(
                f"{self.base_url}/lessons/group/{group_id}", params={"week_id": week_id}
            )
            rows = data if isinstance(data, list) else data.get("data", data.get("items", []))
            for item in rows:
                if not isinstance(item, dict):
                    continue
                lessons.append(
                    Lesson(
                        group=group,
                        date=str(item.get("date", "")),
                        slot=int(item.get("lessonNumber", 0) or 0),
                        subgroup=0,
                        subject=_text(item.get("subject"), "Без названия"),
                        room=_text(item.get("cabinet"), "—"),
                        teacher=_text(item.get("teacher"), ""),
                        time_start=normalize_time(str(item.get("timeStart", ""))),
                        time_end=normalize_time(str(item.get("timeEnd", ""))),
                    )
                )
        return lessons
