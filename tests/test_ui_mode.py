"""The dashboard's mode is settled once per installation (imprint/ui_mode.py, ADR 0182)."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config_schema, ui_mode


def _history(directory: str, name: str, table: str, rows: int) -> None:
    conn = sqlite3.connect(os.path.join(directory, name))
    conn.execute(f"CREATE TABLE {table} (id TEXT)")
    conn.executemany(f"INSERT INTO {table} VALUES (?)", [(str(n),) for n in range(rows)])
    conn.commit()
    conn.close()


class SettleTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.directory = self._dir.name
        env = {k: v for k, v in os.environ.items() if k not in ("IMPRINT_UI_MODE", "IMPRINT_UI_TUTORIALS")}
        patcher = patch.dict(os.environ, {**env, "IMPRINT_DATA_DIR": self.directory}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _config(self) -> dict:
        return json.loads(Path(self.directory, "config.json").read_text())

    def test_a_new_installation_starts_simple_with_tutorials(self):
        self.assertEqual(ui_mode.settle(), "simple")
        self.assertEqual(self._config(), {"ui": {"mode": "simple"}})
        self.assertEqual(ui_mode.state(), {"mode": "simple", "tutorials": True})

    def test_an_installation_that_ran_a_job_keeps_the_advanced_dashboard_without_tutorials(self):
        _history(self.directory, "queue.sqlite3", "jobs", 1)
        self.assertEqual(ui_mode.settle(), "advanced")
        self.assertEqual(self._config(), {"ui": {"mode": "advanced", "tutorials": False}})
        self.assertEqual(ui_mode.state(), {"mode": "advanced", "tutorials": False})

    def test_an_agent_session_counts_as_use_and_empty_stores_do_not(self):
        _history(self.directory, "queue.sqlite3", "jobs", 0)
        self.assertFalse(ui_mode.used_before())
        _history(self.directory, "agent_activity.sqlite3", "agent_sessions", 2)
        self.assertTrue(ui_mode.used_before())

    def test_it_settles_once_so_later_history_changes_nothing(self):
        self.assertEqual(ui_mode.settle(), "simple")
        _history(self.directory, "queue.sqlite3", "jobs", 3)
        self.assertIsNone(ui_mode.settle())
        self.assertEqual(ui_mode.state()["mode"], "simple")

    def test_a_choice_already_made_and_other_settings_are_kept(self):
        _history(self.directory, "queue.sqlite3", "jobs", 1)
        config_schema.write_config({"ui": {"tutorials": True}, "chat": {"provider": "ollama"}})
        self.assertEqual(ui_mode.settle(), "advanced")
        self.assertEqual(self._config(), {"ui": {"tutorials": True, "mode": "advanced"}, "chat": {"provider": "ollama"}})

    def test_an_unreadable_store_is_not_evidence_of_use(self):
        Path(self.directory, "queue.sqlite3").write_bytes(b"not a database")
        self.assertFalse(ui_mode.used_before())

    def test_the_setting_accepts_only_its_two_values(self):
        with self.assertRaises(ValueError):
            config_schema.set_value("ui.mode", "expert")
        self.assertEqual(config_schema.set_value("ui.mode", "advanced"), "advanced")


class UiEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_dashboard_reads_mode_and_tutorials_in_one_request(self):
        import httpx
        from imprint.api import app
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"IMPRINT_DATA_DIR": directory}):
            config_schema.write_config({"ui": {"mode": "advanced", "tutorials": False}})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
                                         base_url="http://127.0.0.1:8420") as client:
                answer = (await client.get("/api/ui")).json()
        self.assertEqual(answer, {"mode": "advanced", "tutorials": False})


if __name__ == "__main__":
    unittest.main()
