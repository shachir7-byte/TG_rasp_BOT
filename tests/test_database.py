import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BOT_TOKEN", "0:test")
os.environ.setdefault("ADMIN_ID", "1")

import database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_file = database.DB_FILE
        database.DB_FILE = str(Path(self.temp_dir.name) / "test.db")
        asyncio.run(database.close_db_connection())

    def tearDown(self):
        asyncio.run(database.close_db_connection())
        database.DB_FILE = self.original_db_file
        self.temp_dir.cleanup()

    def test_save_user_does_not_overwrite_group(self):
        async def scenario():
            await database.init_db()
            await database.save_user(1, "user", "ИС-23/9-3")
            self.assertEqual(await database.get_user_group(1), "ИС-23/9-3")
            await database.save_user(1, "user")
            self.assertEqual(await database.get_user_group(1), "ИС-23/9-3")

        asyncio.run(scenario())

    def test_config_get_set(self):
        async def scenario():
            await database.init_db()
            self.assertIsNone(await database.get_config("missing"))
            await database.set_config("engine", "v2")
            self.assertEqual(await database.get_config("engine"), "v2")
            self.assertEqual(await database.get_selected_engine(), "v2")

        asyncio.run(scenario())

    def test_active_users_for_worker(self):
        async def scenario():
            await database.init_db()
            await database.save_user(1, "u1", "ИС-23/9-3")
            await database.save_user(2, "u2", "ИС-23/9-3")
            await database.update_notify_settings(1, time="08:00", notify_type="time", offset=0)
            await database.update_notify_settings(2, time=None, notify_type="offset", offset=1)

            active = await database.get_active_users_for_worker()
            self.assertEqual(len(active), 2)
            self.assertEqual(active[0]["user_group"], "ИС-23/9-3")

        asyncio.run(scenario())

    def test_migration_renames_legacy_group(self):
        async def scenario():
            connection = await database.get_db_connection()
            await connection.execute(
                "CREATE TABLE users (chat_id INTEGER PRIMARY KEY, username TEXT, "
                "user_group TEXT, notify_time TEXT, notify_type TEXT DEFAULT 'time', "
                "notify_offset INTEGER DEFAULT 0, request_count INTEGER DEFAULT 0, "
                "last_sent_date TEXT, first_seen TEXT)"
            )
            await connection.execute("INSERT INTO users (chat_id, user_group) VALUES (1, 'is')")
            await connection.commit()
            await database.close_db_connection()

            await database.init_db()
            self.assertEqual(await database.get_user_group(1), "ИС-23/9-3")

        asyncio.run(scenario())

    def test_stats(self):
        async def scenario():
            await database.init_db()
            await database.save_user(1, "u1", "ИС-23/9-3")
            await database.log_action(1, "start")
            stats = await database.get_admin_stats()
            self.assertEqual(len(stats), 1)
            self.assertEqual(stats[0]["last_action"], "start")
            self.assertEqual(await database.clear_stats(), 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
