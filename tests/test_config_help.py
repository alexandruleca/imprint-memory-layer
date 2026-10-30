"""Every setting explains what it is for, with an example (imprint/config_help.py)."""
import os
import tempfile
import unittest
from unittest.mock import patch

from imprint.config_help import HELP
from imprint.config_schema import SETTINGS, SETTINGS_BY_KEY


class ConfigHelpTests(unittest.TestCase):
    def test_every_setting_has_an_explanation_and_an_example(self):
        missing = [s.key for s in SETTINGS
                   if not HELP.get(s.key, {}).get("help") or not HELP.get(s.key, {}).get("example")]
        self.assertEqual(missing, [], "explain these in imprint/config_help.py")

    def test_help_names_only_settings_that_exist(self):
        self.assertEqual(sorted(set(HELP) - set(SETTINGS_BY_KEY)), [])


class ConfigHelpAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_the_dashboard_receives_the_explanations(self):
        import httpx
        from imprint.api import app
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"IMPRINT_DATA_DIR": directory}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
                                         base_url="http://127.0.0.1:8420") as client:
                settings = {s["key"]: s for s in (await client.get("/api/config")).json()["settings"]}
        self.assertEqual(settings["model.device"]["help"], HELP["model.device"]["help"])
        self.assertEqual(settings["model.device"]["example"], HELP["model.device"]["example"])


if __name__ == "__main__":
    unittest.main()
