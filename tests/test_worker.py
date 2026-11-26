import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BOT_TOKEN", "0:test")

from handlers.worker import (
    first_lesson_start,
    offset_notify_time,
    seconds_until_next_minute,
)

from engines.base import Lesson


class WorkerHelpersTests(unittest.TestCase):
    def test_offset_notify_time(self):
        self.assertEqual(offset_notify_time("08:30", 2), "06:30")
        self.assertEqual(offset_notify_time("10:00", 1), "09:00")
        self.assertIsNone(offset_notify_time("01:00", 2))
        self.assertIsNone(offset_notify_time(None, 1))
        self.assertIsNone(offset_notify_time("bad", 1))

    def test_first_lesson_start(self):
        lessons = [
            Lesson("g", "2026-11-10", 2, 0, "А", "1", "", "10:10", "11:20"),
            Lesson("g", "2026-11-10", 1, 0, "Б", "1", "", "08:30", "09:50"),
        ]
        self.assertEqual(first_lesson_start(lessons), "08:30")
        self.assertIsNone(first_lesson_start([]))

    def test_seconds_until_next_minute(self):
        self.assertAlmostEqual(seconds_until_next_minute(datetime(2026, 11, 10, 9, 0, 30)), 30.0)
        self.assertEqual(seconds_until_next_minute(datetime(2026, 11, 10, 9, 0, 59)), 1.0)


if __name__ == "__main__":
    unittest.main()
