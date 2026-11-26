import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BOT_TOKEN", "0:test")
os.environ.setdefault("ADMIN_ID", "1")

from engines.base import Lesson
from utils.formatter import format_schedule, is_lesson_now, split_message

NOW = datetime(2026, 11, 10, 9, 40)


def lesson(**kwargs):
    base = {
        "group": "ИС-23/9-3",
        "date": "2026-11-10",
        "slot": 1,
        "subgroup": 0,
        "subject": "Математика",
        "room": "207",
        "teacher": "Иванов И.И.",
        "time_start": "08:30",
        "time_end": "09:50",
    }
    base.update(kwargs)
    return Lesson(**base)


class FormatterTests(unittest.TestCase):
    def test_empty_schedule(self):
        text = format_schedule("Вторник", [], is_week=False, now=NOW)
        self.assertIn("Вторник", text)
        self.assertIn("Пар нет", text)

    def test_day_schedule_renders_lesson(self):
        text = format_schedule("Вторник", [lesson()], is_week=False, now=NOW)
        self.assertIn("Математика", text)
        self.assertIn("08:30–09:50", text)
        self.assertIn("Каб: <code>207</code>", text)

    def test_html_is_escaped(self):
        text = format_schedule(
            "Вторник",
            [lesson(subject="<b>Хак</b> & Ко", teacher="<i>П</i>")],
            is_week=False,
            now=NOW,
        )
        self.assertNotIn("<b>Хак</b>", text)
        self.assertIn("&lt;b&gt;Хак&lt;/b&gt;", text)

    def test_subgroup_rendered(self):
        text = format_schedule("Вторник", [lesson(subgroup=1)], is_week=False, now=NOW)
        self.assertIn("подгруппа 1", text)

    def test_week_groups_by_day(self):
        lessons = [
            lesson(date="2026-11-09", subject="А"),
            lesson(date="2026-11-10", subject="Б"),
        ]
        text = format_schedule("Неделя", lessons, is_week=True, now=NOW)
        self.assertIn("🗓 Пн | 09.11", text)
        self.assertIn("🗓 Вт | 10.11", text)

    def test_is_lesson_now(self):
        self.assertTrue(is_lesson_now(lesson(), NOW))
        self.assertFalse(is_lesson_now(lesson(time_start="10:00", time_end="11:20"), NOW))
        self.assertFalse(is_lesson_now(lesson(time_start="", time_end=""), NOW))

    def test_is_lesson_now_aware_datetime(self):
        from datetime import timedelta, timezone

        aware = datetime(2026, 11, 10, 9, 40, tzinfo=timezone(timedelta(hours=5)))
        self.assertTrue(is_lesson_now(lesson(), aware))

    def test_split_message(self):
        self.assertEqual(split_message("короткое"), ["короткое"])
        blocks = ["блок номер " + str(i) for i in range(500)]
        text = "\n\n".join(blocks)
        parts = split_message(text, limit=1000)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 1000 for part in parts))


if __name__ == "__main__":
    unittest.main()
