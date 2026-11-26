from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Building:
    id: str
    name: str
    groups: list[str] = field(default_factory=list)
    rooms: list[str] = field(default_factory=list)


@dataclass
class EngineIndex:
    weeks: list[str]
    week: str
    from_date: str
    to_date: str
    generated_at: str | None = None
    buildings: list[Building] = field(default_factory=list)
    teachers: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Lesson:
    group: str
    date: str
    slot: int
    subgroup: int
    subject: str
    room: str
    teacher: str
    time_start: str
    time_end: str


class ScheduleUnavailable(RuntimeError):
    pass


def normalize_time(value: str) -> str:
    """'08:30:00' -> '08:30', '08:30' -> '08:30', '' -> ''."""
    text = (value or "").strip()
    if len(text) >= 5 and text[2] == ":":
        return text[:5]
    return text


class ScheduleEngine(ABC):
    key: str = ""
    name: str = ""
    description: str = ""
    primary: bool = False

    @abstractmethod
    async def fetch_index(self) -> EngineIndex:
        raise NotImplementedError

    @abstractmethod
    async def fetch_lessons(self, building: Building, week: str) -> list[Lesson]:
        raise NotImplementedError

    def label(self) -> str:
        return f"{self.key} ({self.name})"
