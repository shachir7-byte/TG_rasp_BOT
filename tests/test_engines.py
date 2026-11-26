import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BOT_TOKEN", "0:test")
os.environ.setdefault("ADMIN_ID", "1")

from aiohttp import web

import engines
import engines.session as session
from engines.base import Building, EngineIndex, Lesson, ScheduleUnavailable, normalize_time
from engines.legacy import LegacyEngine
from engines.web import WebEngine

INDEX = {
    "weeks": ["2026-11-02", "2026-11-09"],
    "week": "2026-11-09",
    "from": "2026-11-09",
    "to": "2026-11-14",
    "generatedAt": "2026-11-09T17:00:00+00:00",
    "buildings": [
        {"id": "b7", "name": "Пролетарский", "groups": ["ИС-23/9-3", "ИС-23/9-2"], "rooms": ["207"]},
        {"id": "b5", "name": "Мелик-Карамова", "groups": ["Д-23/9-1"], "rooms": []},
    ],
    "teachers": {"Мацюк Н.А.": ["b7"]},
}

LESSONS = [
    ["ИС-23/9-3", "2026-11-09", 2, 0, "Проектирование", "207", "Мацюк Н.А.", "09:40–10:40"],
    ["ИС-23/9-3", "2026-11-10", 1, 0, "Математика", "208", "Иванов", "08:30–09:50"],
    ["ИС-23/9-2", "2026-11-09", 2, 0, "Физика", "209", "Петров", "09:40–10:40"],
]


def reset_caches():
    engines._index_cache.clear()
    engines._lessons_cache.clear()


class WebEngineParseTests(unittest.TestCase):
    def setUp(self):
        reset_caches()
        self.engine = WebEngine("https://example.test/api/schedule")

    def test_parse_index(self):
        with mock.patch(
            "engines.web.request_json", new=mock.AsyncMock(return_value=INDEX)
        ):
            index = asyncio.run(self.engine.fetch_index())
        self.assertEqual(len(index.buildings), 2)
        self.assertEqual(index.week, "2026-11-09")
        self.assertEqual(index.buildings[0].name, "Пролетарский")
        self.assertEqual(index.buildings[0].groups[0], "ИС-23/9-3")

    def test_parse_lessons_splits_time(self):
        async def fake(url, params=None):
            return {"lessons": LESSONS}

        with mock.patch("engines.web.request_json", new=fake):
            lessons = asyncio.run(self.engine.fetch_lessons(Building("b7", "Пролетарский"), "2026-11-09"))
        self.assertEqual(len(lessons), 3)
        self.assertEqual(lessons[0].time_start, "09:40")
        self.assertEqual(lessons[0].time_end, "10:40")
        self.assertEqual(lessons[0].subgroup, 0)


class NormalizeTimeTests(unittest.TestCase):
    def test_hh_mm_ss_trimmed(self):
        self.assertEqual(normalize_time("08:30:00"), "08:30")

    def test_hh_mm_unchanged(self):
        self.assertEqual(normalize_time("08:30"), "08:30")

    def test_empty(self):
        self.assertEqual(normalize_time(""), "")

    def test_none_safe(self):
        self.assertEqual(normalize_time(None), "")

    def test_en_dash_time_unchanged(self):
        # _split_time уже разбил "09:40–10:40" на два значения до normalize_time
        self.assertEqual(normalize_time("09:40"), "09:40")


class LegacyEngineTests(unittest.TestCase):
    def setUp(self):
        reset_caches()
        self.engine = LegacyEngine("https://example.test")

    def test_index_synthesizes_groups(self):
        index = asyncio.run(self.engine.fetch_index())
        self.assertEqual(len(index.buildings), 1)
        self.assertIn("ИС-23/9-3", index.buildings[0].groups)
        self.assertIn("Д-23/9-1", index.buildings[0].groups)


class FakeEngine:
    primary = False

    def __init__(self, key, index=None, lessons=None, fail=False):
        self.key = key
        self.name = key
        self.description = ""
        self.index = index
        self.lessons = lessons or []
        self.fail = fail

    async def fetch_index(self):
        if self.fail:
            raise ScheduleUnavailable("down")
        return self.index

    async def fetch_lessons(self, building, week):
        if self.fail:
            raise ScheduleUnavailable("down")
        return self.lessons


def make_index():
    return EngineIndex(
        weeks=["2026-11-09"],
        week="2026-11-09",
        from_date="2026-11-09",
        to_date="2026-11-14",
        buildings=[Building("b7", "Пролетарский", groups=["ИС-23/9-3"])],
    )


def make_lessons():
    return [
        Lesson("ИС-23/9-3", "2026-11-09", 1, 0, "Математика", "207", "Иванов", "08:30", "09:50"),
        Lesson("ИС-23/9-2", "2026-11-09", 1, 0, "Физика", "208", "Петров", "08:30", "09:50"),
    ]


class ManagerTests(unittest.TestCase):
    def setUp(self):
        reset_caches()
        self.original_engines = engines.ENGINES
        engines.ENGINES = {"v2": FakeEngine("v2", make_index(), make_lessons(), fail=False)}

    def tearDown(self):
        engines.ENGINES = self.original_engines
        reset_caches()

    def test_group_schedule_filters_by_group(self):
        lessons, engine = asyncio.run(engines.fetch_group_schedule("ИС-23/9-3", "2026-11-09"))
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0].subject, "Математика")
        self.assertEqual(engine.key, "v2")

    def test_unknown_group_raises(self):
        with self.assertRaises(ScheduleUnavailable):
            asyncio.run(engines.fetch_group_schedule("НЕТ", "2026-11-09"))

    def test_fallback_and_last_responded(self):
        engines.ENGINES = {
            "v2": FakeEngine("v2", make_index(), make_lessons(), fail=True),
            "v1": FakeEngine("v1", make_index(), make_lessons(), fail=False),
        }
        lessons, engine = asyncio.run(engines.fetch_group_schedule("ИС-23/9-3", "2026-11-09"))
        self.assertEqual(engine.key, "v1")
        self.assertEqual(len(lessons), 1)
        self.assertEqual(engines.last_responded()["engine"], "v1")


class HttpPipelineTests(unittest.TestCase):
    def setUp(self):
        reset_caches()
        self.original_engines = engines.ENGINES

    def tearDown(self):
        engines.ENGINES = self.original_engines
        reset_caches()

    def test_full_pipeline_over_http(self):
        async def scenario():
            app = web.Application()
            app.router.add_get("/api/schedule/", self._index)
            app.router.add_get("/api/schedule/{building_id}/", self._lessons)

            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]

            engines.ENGINES = {"v2": WebEngine(f"http://127.0.0.1:{port}/api/schedule")}
            await engines.init_session()
            try:
                lessons, engine = await engines.fetch_group_schedule("ИС-23/9-3", "2026-11-09")
                self.assertEqual(engine.key, "v2")
                self.assertEqual(len(lessons), 2)
                self.assertEqual(lessons[0].subject, "Проектирование")
                self.assertEqual(lessons[0].time_start, "09:40")
            finally:
                await engines.close_session()
                await runner.cleanup()

        asyncio.run(scenario())

    async def _index(self, request):
        return web.json_response(INDEX)

    async def _lessons(self, request):
        return web.json_response({"lessons": LESSONS})


if __name__ == "__main__":
    unittest.main()
