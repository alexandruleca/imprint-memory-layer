"""The simple dashboard's settings are choices over real settings (imprint/config_simple.py, ADR 0182)."""
import os
import tempfile
import unittest
from unittest.mock import patch

from imprint import config_schema, config_simple
from imprint.config_schema import SETTINGS_BY_KEY


class CatalogShapeTests(unittest.TestCase):
    def test_every_item_names_settings_that_exist(self):
        for item in config_simple.ITEMS.values():
            for key in config_simple.keys(item):
                self.assertIn(key, SETTINGS_BY_KEY, item.id)

    def test_a_switch_is_a_boolean_setting(self):
        for item in config_simple.ITEMS.values():
            if item.key:
                self.assertIs(SETTINGS_BY_KEY[item.key].type, bool, item.id)

    def test_every_option_gives_each_key_a_value_the_setting_accepts(self):
        for item in config_simple.ITEMS.values():
            if item.own_choices or not item.options:
                continue
            names = set(config_simple.keys(item))
            for option in item.options:
                self.assertEqual(set(option.values), names, f"{item.id}: {option.label}")
                for key, value in option.values.items():
                    setting = SETTINGS_BY_KEY[key]
                    self.assertEqual(config_schema._cast(str(value), setting.type), value, f"{item.id}: {key}")
                    if setting.choices and not setting.custom:
                        self.assertIn(value, {choice.value for choice in setting.options()}, f"{item.id}: {key}")

    def test_one_option_of_every_preset_is_the_defaults(self):
        for item in config_simple.ITEMS.values():
            if item.own_choices or not item.options:
                continue
            defaults = {key: SETTINGS_BY_KEY[key].default for key in config_simple.keys(item)}
            self.assertIn(defaults, [option.values for option in item.options], item.id)

    def test_ids_are_unique_and_conditions_name_settings(self):
        ids = [item.id for group in config_simple.GROUPS for item in group.items]
        self.assertEqual(len(ids), len(set(ids)))
        for item in config_simple.ITEMS.values():
            for key, _ in item.when:
                self.assertIn(key, SETTINGS_BY_KEY, item.id)


class CatalogStateTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("IMPRINT_") and k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")}
        patcher = patch.dict(os.environ, {**env, "IMPRINT_DATA_DIR": self._dir.name}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _item(self, item_id: str) -> dict:
        return next(item for group in config_simple.catalog() for item in group["items"] if item["id"] == item_id)

    def test_defaults_show_as_their_options(self):
        self.assertEqual(self._item("assistant-runs")["selected"], 0)
        self.assertEqual(self._item("assistant-depth")["selected"], 1)
        self.assertEqual(self._item("recordings-quality")["selected"], 0)
        self.assertIs(self._item("recordings-voices")["on"], True)
        self.assertIs(self._item("tidy-labels")["on"], False)

    def test_a_preset_writes_every_key_and_reads_back_selected(self):
        keys = config_simple.apply("assistant-depth", option=2)
        self.assertEqual(sorted(keys), ["chat.research_timeout_sec", "chat.research_token_budget"])
        self.assertEqual(config_schema.resolve("chat.research_timeout_sec"), (900, "config"))
        self.assertEqual(config_schema.resolve("chat.research_token_budget"), (196608, "config"))
        self.assertEqual(self._item("assistant-depth")["selected"], 2)

    def test_values_set_in_the_advanced_dashboard_show_as_custom_and_are_left_alone(self):
        config_schema.set_value("chat.research_token_budget", "100000")
        self.assertIsNone(self._item("assistant-depth")["selected"])
        self.assertEqual(config_schema.resolve("chat.research_token_budget")[0], 100000)
        config_schema.set_value("chat.provider", "ollama")
        self.assertIsNone(self._item("assistant-runs")["selected"])

    def test_a_switch_writes_its_setting(self):
        self.assertEqual(config_simple.apply("tidy-labels", on=True), ["tagger.llm"])
        self.assertEqual(config_schema.resolve("tagger.llm"), (True, "config"))
        self.assertIs(self._item("tidy-labels")["on"], True)

    def test_an_item_shows_only_while_its_condition_holds(self):
        self.assertEqual(self._item("assistant-model")["kind"], "choice")
        config_schema.set_value("chat.provider", "ollama")
        with self.assertRaises(StopIteration):
            self._item("assistant-model")

    def test_a_provider_without_its_key_is_unavailable_and_refused(self):
        option = self._item("assistant-runs")["options"][1]
        self.assertIn("ANTHROPIC_API_KEY", option["unavailable"])
        with self.assertRaises(ValueError):
            config_simple.apply("assistant-runs", option=1)
        self.assertEqual(config_schema.resolve("chat.provider")[0], "local")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "set"}):
            self.assertEqual(self._item("assistant-runs")["options"][1]["unavailable"], "")
            config_simple.apply("assistant-runs", option=1)
        self.assertEqual(config_schema.resolve("chat.provider")[0], "anthropic")

    def test_a_setting_an_environment_variable_holds_is_locked(self):
        with patch.dict(os.environ, {"IMPRINT_AUDIO_DIARIZE": "0"}):
            item = self._item("recordings-voices")
        self.assertEqual(item["locked"], "IMPRINT_AUDIO_DIARIZE")
        self.assertIs(item["on"], False)

    def test_the_report_names_what_leaves_the_computer(self):
        report = self._item("privacy-leaves")
        self.assertIs(report["ok"], True)
        self.assertEqual(report["lines"], ["Nothing. Chat, labels and recaps all run on this computer."])
        config_schema.set_value("chat.provider", "anthropic")
        config_schema.set_value("tagger.llm", "true")
        config_schema.set_value("tagger.llm_provider", "openai")
        config_schema.set_value("summarizer.enabled", "true")
        report = self._item("privacy-leaves")
        self.assertIs(report["ok"], False)
        self.assertEqual(len(report["lines"]), 2)
        self.assertIn("Anthropic", report["lines"][0])
        self.assertIn("OpenAI", report["lines"][1])

    def test_unknown_items_and_bad_choices_are_refused(self):
        with self.assertRaises(KeyError):
            config_simple.apply("no-such-item", on=True)
        with self.assertRaises(KeyError):
            config_simple.apply("app-theme", on=True)
        with self.assertRaises(ValueError):
            config_simple.apply("assistant-depth", option=9)
        with self.assertRaises(ValueError):
            config_simple.apply("tidy-labels", option=0)


class SimpleSettingsAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_the_dashboard_reads_the_groups_and_applies_a_choice(self):
        import httpx
        from imprint.api import app
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"IMPRINT_DATA_DIR": directory}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
                                         base_url="http://127.0.0.1:8420") as client:
                groups = (await client.get("/api/settings/simple")).json()["groups"]
                self.assertEqual([group["id"] for group in groups],
                                 ["assistant", "echo", "recordings", "tidy", "privacy", "devices", "app"])
                answer = await client.put("/api/settings/simple/recordings-quality", json={"option": 1})
                self.assertEqual(answer.status_code, 200)
                self.assertEqual(answer.json()["keys"], ["audio.model"])
                self.assertEqual(config_schema.resolve("audio.model"), ("large-v3-turbo", "config"))
                refused = await client.put("/api/settings/simple/recordings-quality", json={"option": 7})
                self.assertEqual(refused.status_code, 400)
                missing = await client.put("/api/settings/simple/nope", json={"on": True})
                self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
